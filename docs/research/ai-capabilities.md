# What else the chat's agent could do

November 2026 (surveyed on the 18th, against `dev` at `5bb5dcfc`). The
question: which of the app's capabilities exist in code but are not tools
of the chat's agent, starting with drawing on the page; what each would
reuse, what is missing, and which should stay out. How the tools work now
is in [dev/ai_tools.md](../dev/ai_tools.md); this note names only what
they do not do.

## What the agent reaches today

It reads and looks (pages, notes, chats, annotations, PDF pages, handwriting
and pictures as pictures, citation records), searches the library and the
web, organizes the library in a folder chat, writes note text
(`edit_block`, `create_block`, `move_block`, `delete_block`), stores a
picture of a region (`clip_region`), and comments, resolves and highlights a
quoted passage or a file's lines. Every tool runs on the server. The app
only reacts to an action that names the page already open: it rings a
block, sweeps the outline, or reloads the tree and scrolls to the block
(`handleAgentEvent`, `frontend/src/app/App.jsx:5895`; `BLOCK_TOOLS`,
`chat/agentSteps.js:24`). Nothing the agent makes appears on a page's
surface except a text highlight.

## Drawing and things placed on a page

Each placed thing is a block with typed properties, and each can be written
on the server as one `insert` through `ops.apply_ops`, so it is logged,
fanned out to whoever has the page open, and merged like any edit. The
Sketchbook seed already does all of it on the server: it stores `.ink`
files and sends one `apply_ops` batch of sheets, ink groups and text boxes
(`seed.py:103`, `_seed_sketchbook`; the drawings come from
`sketchbook.py`).

| Thing | Stored as | Server code that exists | Missing | Verdict |
|---|---|---|---|---|
| Ink shapes | a block with `ink_url` (a stored gamma-ink v2 file), `ink_strokes`, and on a PDF page `pdf_position` at the page's top level; on a sheet a child of the sheet ([handwriting.md](../dev/handwriting.md) "The stroke file") | `SHAPE_KINDS`: line, arrow, rect, ellipse, triangle, polygon, curve (`ink.py:81`), the closed ones with a `fill`, a curve up to 31 points; `ink.shape_samples` (`:1768`) bakes a shape's samples, `group_files` (`:1879`) builds and splits the file, `pdf_position` (`:524`); a shape stroke's template in `notability_map.py:764`; `ink_ai.read_shapes` (`ink_ai.py:512`) already reads a model's shape records leniently; `ops._Batch.merge_ink` (`ops.py:591`) merges strokes into an existing group against a pen writing at the same time | an executor. "Redraw as shapes" sends the model's shapes to the client, which writes them (`ink/inkAi.js` `redrawPlan`, then `ink/useInkDocument.js` `applyInk`); no server function writes strokes onto a page | moderate |
| Lettering in ink | strokes; the file has no text kind | the Sketchbook's single-line capitals (`GLYPHS`, `sketchbook.py:235`, `text_width`) and its SVG path flattener (`flatten`, `:115`) | nothing, but a text box reads better and is searchable | not worth a tool of its own |
| Text box | `text_box: {x, y, w, h, auto, size, color, bg}` with `pdf_page`, the content Markdown ([text_boxes.md](../dev/text_boxes.md)) | `normalize_text_box` (`text_box.py:82`), `measure` (`:363`) for a box sized on the server, `keep_on_surface` (`:134`); made on the server by the Sketchbook, the Notability import and the `/FreeText` import | an executor | easy |
| Picture on a page | `picture: {url, x, y, w, h, angle, crop, …}` with `pdf_page` ([pictures.md](../dev/pictures.md)) | `clip_region`'s upload (`ai_pictures.store_picture`, `:149`) is a valid `url` as it is | for an SVG: `store_picture` runs Pillow only, so it needs `storage.store_file` with `pdf_image`'s check and `picture.svg_size` | easy for a raster picture, moderate for SVG |
| Sheet of paper | a block with `sheet: <paper>` and `collapsed: true`, the title its content; no file ([notebooks.md](../dev/notebooks.md)) | `notebook.normalize_paper` (`:37`), the Sketchbook's sheets | an executor | easy |
| Area highlight | `pdf_position` with `area: true` and no quote, as the viewer makes it (`PdfViewer.jsx:2270`) | `hand._mark` (`annotations/hand.py:189`) inserts any highlight's properties under the agent's stamp; `highlights.position(…, area=True)` (`highlights.py:147`) with the page's `pdf_text.page_sizes` (`:428`) | a `box` argument on `highlight`, which today requires a quote (`places.find`) | easy |
| Diagram | a ```` ```mermaid ```` or ```` ```svg ```` fence in a note ([diagrams.md](../dev/diagrams.md)) | the agent writes one already with `create_block` | a check before writing: Mermaid parses only in the client; an SVG could be drawn with `pdf_image` in its child process | already reachable |

What all of them share:

- **Revert.** `_plan_create` (`ai_revert.py:106`) puts back only `content`
  on a redo and refuses an undo when the content changed, so a block made
  with properties would come back from a redo as a plain note. A placed
  thing's revert record needs its properties, as `delete_block`'s already
  carries them. An agent's strokes merged into a person's group revert as
  those strokes taken out (their ids), a kind of its own.
- **The frame.** On a PDF page the frame is the pdf.js viewport at scale 1
  with the rotation applied, top-left origin, in points; on a sheet, points
  from its corner. A tool that takes page fractions, as `clip_region`'s
  `box` does, and converts with `page_sizes` keeps the model out of points
  and rotation.
- **Aim.** A model places poorly by coordinates read off a picture. The code
  suggests a better anchor: `places.find` already finds a quote's
  rectangles, so "a box in the margin beside this passage", "circle this
  figure" (the figure's caption as the quote) or "an arrow from here to
  there" can be placed from quotes, with a box in fractions as the fallback.
  The result should carry a picture of the page as drawn (the `ink=1`
  rendering, `ink_view.page_with_handwriting`) so the model sees what it
  made.
- **Where it travels.** Each counts against the workspace's quota (507 when
  full), and an ink file is capped at 6 MB and 5,000 strokes. A workspace
  linked to Zotero sends area highlights, ink and text boxes to Zotero as
  annotations (`zotero_annotations.py:742`, `:822`, `:835`); pictures are
  not kept there. The annotated PDF carries them as `/Square`, `/Ink`,
  `/FreeText` and `/Stamp`.
- **Who made it.** Highlights carry the agent's stamp (`agents.stamp`
  through `hand._mark`); placed things should too, so a person can tell the
  agent's drawing from their own.

## Library and page features

| Capability | Code that exists | Reachable now | Verdict |
|---|---|---|---|
| A new page of notes, or a sub-page | `POST /pages` (`routers/pages.py:134`) over `blocks_store.create_page` (`:1135`), with `parent_page` or `folders` | no: only `save_paper` makes a page. No guardrail forbids it; like `save_paper` it would take an existing folder or a parent page. In a page chat a new top-level page is out of the chat's reach, a sub-page of its page is not | easy |
| Refresh or correct a page's metadata, pin its citation key | `POST /metadata/fetch` (`routers/metadata.py:1406`, `force` to look again), `/metadata/update` (`:1882`), `/metadata/verify` (`:1952`) | partly: `cite` reads the record, never changes it. `_save_props` (`:1207`) writes the page row directly, not through `ops.apply_ops`, so there is no op and no revert; a tool needs its own revert record of the keys it changed | refresh easy, edits moderate |
| Export a page, a folder or a bibliography | `mcp_export.export_page` (`:59`); `GET /pages/{id}/export`, `/export-pdf`, `/folders/{id}/export`, `/bibliography` (`routers/export.py:1596`, `:1628`, `:1685`, `:1719`) | no in the chat; MCP has `export_page` | easy as a download link in the reply, moderate as a file card |
| Transcribe a recording | `POST /jobs/transcribe` (`routers/recordings.py:29`) | reading a transcript yes (`read_block`); starting one no. Job routes take a session account only, so a tool calls the job code in the server. It spends the account's transcription provider | easy to moderate |
| Translate a PDF | the job runs in the browser (`pdf/translateJob.js`); the server translates chunks and keeps them by a hash of each paragraph | no. As a server tool it means moving the job to the server; as a command of the open page it is there already (below) | hard on the server, easy as a UI command |
| Background tasks | `routers/jobs.py` lists, reads and cancels; the kinds are in [tasks.md](../dev/tasks.md) | no | a read-only list easy; starting only metadata, transcription and relocation |
| Recolour or restyle a highlight | `PUT /blocks/{id}` writes `color` and `style` | partly: `highlight` sets them only when it makes one, `edit_block` changes the note alone | easy, with the properties revert above |
| Find highlights again after a PDF is replaced | `POST /jobs/relocate-highlights` (`routers/pages.py:922`) | `list_annotations` names lost and rough ones | the job moderate; accepting or moving one edits its anchor, which the guardrails forbid |
| Clip a web page into notes | `POST /clip` with an `article` (`routers/clip.py:401`, `web_clip.article_blocks`) | no: `save_paper` sends no article, so a web page saves as a page holding its address alone (`ai_tools/web.py`) | moderate, under Save papers or its own permission |
| What links to a page | `GET /blocks/{id}/backlinks` (`routers/blocks.py:622`) | no | easy, read-only |
| Search across chats | nothing; `read_chats` reads one bucket | no | moderate |
| Collapse or expand a block | the `collapsed` property | no | easy, little use |
| Sync with Zotero now | `POST /zotero-sync/{ws}/sync` (`routers/zotero_sync.py:165`) | no | easy, close to the settings guardrail; little use |
| Reminders and person mentions | plain text ([mentions.md](../dev/mentions.md)): `@2026-10-09 14:30 (remind @alice)`, `@alice`, `[[id]]` | yes: `create_block` and `edit_block` write them, though the prompt never names the syntax | already reachable (see below) |

One finding is not a missing tool: a person the agent mentions in a note
hears of it. The note tools go through `ops.apply_ops`, which records the
people a block's text names for the Inbox (`ops.py:385`), so `@alice` in a
block the agent writes puts an item in Alice's Inbox, and a reminder for
her reaches her. No permission covers that beyond Edit note blocks. The
guardrails keep other users' data out of reach; whether the agent may
address other people is a decision of its own, whatever tools come next.

## Driving the interface

No tool acts in the app; the pieces for one exist.

- **The server can wait for the client.** The approval gate
  (`ApprovalGate`, `ai_agent.py:668`) yields an `approval` line and waits
  on `ai_permissions.wait_for`; the browser handoff (`PaperWait`, `:733`)
  yields a `handoff` and waits until the client posts the fetched file
  back (`routers/ai_handoffs.py`). A tool that asks the app for an answer
  (the PDF page in view, the open tabs) would be a third hook of the same
  kind: a stream line, the client acts, a POST with the result.
- **A command that needs no answer needs less:** a stream line the app acts
  on, as `handleAgentEvent` already does.
- **What the app can do on request:** `openPage(id, target)` with a
  citation target (a PDF page and a quote) and `openBlock(id)`
  (`shared/ui/Widgets.jsx:406`), the ones a reply's links use when clicked;
  the command catalog, 44 app commands (`app/appCommands.js`) and 26 editor
  commands, each with `run(ctx)` and `when(ctx)`: `splitTab`, the toggles
  of the chat, notes, PDF, comments and Files windows, `openSettings(pane)`,
  `exportAs`, and the translated view's `page()` and `document()`; the
  tours' anchors (`guide/anchors.js` `ANCHORS`, `anchorElement`, and
  `revealAnchor` in `useGuide.js:150`, which opens closed menus on the way).
- **What the chat does not know:** the PDF page in view, the open tabs and
  groups, the scroll, which windows are shown. "Attach what I see" sends a
  picture only when the person adds it.

| Candidate | Reuses | Verdict |
|---|---|---|
| Show a page: open it, jump its PDF to a page or a quote, scroll the notes to a block | `openPage` with a citation target, `openBlock` | easy |
| Open Settings at a pane | `openSettings(pane)` | easy |
| Translate the open PDF | the catalog's `translation.document()` | easy, while the page is open |
| Open a page beside the current one | `splitTab` after `openBlock`; there is no "split with page P" | moderate |
| Point at a control ("where is export?") | the tours' spotlight, which would need an entry point of its own outside a tour | moderate |
| Run a command from an allowlist | the catalog; many commands open dialogs or delete, so a list is needed | moderate |
| Ask what the person is looking at | a new wait hook and route | moderate; sending the PDF page in view with the message, as `focus_block_id` goes, is cheaper |

The limits: these work only in a streamed in-app chat, never over MCP, for
a computer's agent or in a request without a stream; a folder chat may have
no page open; another device of the same account sees nothing; a phone has
no groups. The tools a conversation is offered must stay the same while it
lasts, for the prompt cache ([dev/ai.md](../dev/ai.md#prompt-caching)), so
they are offered by where the chat runs, not by what is open at the moment.

## What should stay out

- **Handing work to a computer's agent.** Only the owner's own message or
  comment starts its turn; a comment of the agent's own or a token's starts
  nothing ([dev/local_agents.md](../dev/local_agents.md)). That is a trust
  boundary, not a missing tool.
- **Accepting or moving a relocated highlight**, and making PDF link regions
  (`link_url` blocks): both edit anchors, which the guardrails forbid.
- **Restore, snapshot and delete jobs, settings, sharing, Zotero
  conflicts**: the guardrails' "settings, share links, other users' data".
- **Image generation**: declined in [ai-pictures.md](ai-pictures.md).

## Recommended order

1. An area highlight (`box` on `highlight`) and a text box: the smallest,
   on `hand._mark` and `text_box`'s helpers, and the commonest asks ("box
   this figure", "write a margin note here"). They need the properties
   revert first.
2. `draw`: shapes on a PDF page or a sheet, placed from quotes where it
   can, through `shape_samples`, `group_files` and `merge_ink`, the result
   shown back as a picture. Sheets and placed pictures come with it at
   little cost. A permission of its own in "Make changes" (asking by
   default) fits better than Edit note blocks: what it makes shows on the
   PDF, in exports and in Zotero. The area highlight stays under Comments
   and highlights with `highlight`.
3. Showing a page in the app: a stream line the app acts on.
4. A new page or sub-page, export links, what links to a page.
5. Refreshing and correcting metadata, with its own revert record.

The rest waits for a person to ask for it.

## As built

Branch `ai-chat-capablity`, November 2026: the order above, 1 to 4. How
each works is in [dev/ai_tools.md](../dev/ai_tools.md) ("Drawing on pages",
"show_page", "create_page", "export", "backlinks"); what differs from this
note's proposal:

- **The revert keeps properties and asks before losing work.** A made
  block's record carries `props` for its redo, and its undo stops when its
  text changed, notes are under it, it has comment threads, or strokes were
  drawn into a drawing (its stroke file is another). A move or a restyle
  does not stop it: nothing of anyone's is lost. Comment threads now stop a
  note's revert too.
- **`draw` makes a new group a call**, never merged into a person's: its
  revert is then a block deletion. Shapes go through the redraw's parser
  (`ink_ai.figure`), and a quote anchors a rect or an ellipse around it and
  either end of a line or an arrow. Every anchor must be on the call's page.
- **Text boxes and pictures `near` a passage** go in the empty space the
  annotated PDF's notes use (`pdf_free_space.spot`), the page's handwriting,
  boxes and pictures counted as taken; with none free the call is refused
  rather than laid over the text.
- **`create_page` is a folder chat's**: a page chat's note tools edit its
  own page alone, so it could not fill a page it made. Its revert moves the
  page to Recently deleted, and stops while the page holds anything.
- **Exports are the Export dialog's job**, not download links: the desktop
  app may open a link in a browser without the session. The live chat's app
  adopts the job and downloads its file.
- **`show_page` acts only while its chat is on screen**, at once on the page
  the chat is beside, else when the reply ends on its own: the chat follows
  the open page, so a page opened mid-reply would hide the reply.
- **Backlinks** match `[[id]]` with `instr`: the route's `LIKE` read an
  id's `_` as a wildcard.

Still open, as listed above: metadata refresh and correction (the page row
is written outside the op log), transcription, translating from the chat,
web clipping, a page beside the current one, pointing at a control, a
command runner, what the person is looking at, and lettering in ink.
