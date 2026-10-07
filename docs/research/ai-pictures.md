# Pictures in the AI chat: what the chat sent, what it cost, and the redesign

A survey of how pictures reached the model before October 2026, the limits
the code imposed and why they no longer held, and the design that replaced
them. Read from the code (`gamma/ai_context.py`, `gamma/ai_tools.py`,
`gamma/routers/ai.py`, `gamma/ai_protocols/*`, `frontend/src/chat/ChatDock.jsx`,
`frontend/src/app/App.jsx`, `frontend/src/pdf/PdfViewer.jsx`) and from the
providers' published limits. The mechanics as built are in
[docs/dev/ai.md](../dev/ai.md) under "Pictures" and in
[ai_tools.md](../dev/ai_tools.md); this note is the reasoning behind them.

## What the code did

Five paths put a picture in front of the model, and no path brought one
back out.

| path | who drew it | cap | kept where |
|---|---|---|---|
| paste, drop or pick in the composer | the browser, as a data URL | 4 per message, 6 MB each | base64 inside the chat JSON, re-saved on every 500 ms autosave |
| a Ctrl+drag rectangle, or a click on an area highlight | pdf.js in the browser (`pageRaster.cropPage`) | the same 4 | the same |
| a text selection whose text reads as a formula or table | pdfium on the server (`selection_crops`) | 3 per message | nothing stored; the crop box saved on the reply so the pill could draw it again |
| area highlights in the notes of a context page | pdfium (`render_area_crops`) | 4 per page | nothing |
| attached handwriting chips | pdfium over the annotated export (`ink_view`) | 2 per message | nothing |
| `view_pdf_page`, `view_ink`, area crops on `read_page` | pdfium | — | the tool message only; the chip never |

Three things followed from that shape.

- **Pictures were one-shot.** The request's history carried `{role, text,
  actions}` and nothing else, so a follow-up about a pasted figure answered
  blind. The user saw the figure in the bubble; the model did not.
- **Nothing was normalized.** `parse_images` regex-checked the data URL and
  forwarded the bytes. A 6 MB pasted PNG went to Anthropic as it was and
  exceeded its per-image limit (3.75 MB); four of them made a 32 MB body.
  The composer's caps were working around the wires, not protecting the
  model. Tool pictures, by contrast, were rendered at 1568 px and saved as
  JPEG at quality 85.
- **Two crop pipelines drew the same region differently**: the browser's
  pdf.js crop for area highlights, pdfium's for selections, with different
  resolutions and no handwriting on either.
- **The model could not see pictures already in the notes.** A note's
  `![alt](/api/uploads/<hash>.png)` reached it as that markdown line, and no
  tool opened the file. Nor could it put a picture into a note: the note
  tools take any markdown, but the model never held a URL to write.
- **The user never saw what a tool looked at.** `view_pdf_page`'s picture
  went to the model once and was dropped from the chip.

## What the providers take

The caps were not provider limits. At the render size every tool picture
already used, one page costs about as much as its extracted text.

| provider | pictures per request | one 1568 px page, prompt tokens |
|---|---|---|
| Anthropic Messages API | 100; 3.75 MB and 8000 px each; pixels ÷ 750 | about 1,600 at most (a 1.15 MP cap) |
| OpenAI Chat Completions and Responses | hundreds; 20 MB each | about 800 on the high-detail tile tariff |
| Gemini | 3,000; 258 tokens per 768 px tile | about 1,000 |

Codex and Claude Code attach screenshots freely, keep them in the transcript
and let compaction handle overflow. Gamma already had the pre-flight window
estimate that drops old turns ([ai.md](../dev/ai.md) "Fitting the window"),
so the per-source caps were doing a job something else did.

## The design

One shape, one size, one budget, one store — and a way out.

1. **One shape.** A message's `images` are picture records: a stored upload
   `{kind, url, width, height}` or a region of a PDF page `{kind: "area" |
   "view", page_id, page, box, ink?}` the server renders on demand. The
   browser never ships pixels for a region; the area highlight's pdf.js crop
   went. An older chat's data URL is still shown and still read when sent
   (`data_url_picture`), never stored.
2. **One size.** Every picture passes through `ai_pictures.normalize` on
   the way in: the longer side at most `RENDER_MAX_SIDE`, JPEG unless small
   or translucent. That is what makes a per-picture token cost a fair bound
   (`Protocol.picture_tokens`: 1,600 on Anthropic's wire, 800 on OpenAI's)
   and what keeps a pasted screenshot from being refused upstream.
3. **One budget.** `max_pictures` on the request (Settings → AI → Chat →
   "Pictures per message", default 12) covers the message's pictures in
   priority order: the user's own, selection crops, attached handwriting,
   the context pages' area highlights. The per-page area cap survives only
   as a guard against a page covered in rectangles (12). The newest pictures
   of earlier turns stay in the conversation under the same number, each
   re-read from its URL; older ones are named in a line and not sent again.
   Every picture gets a label line in the turn ("Picture 2: a region the
   user marked on PDF page 4 of …, stored at …"), which is what lets the
   model and the user point at one, and what tells the model which URLs it
   may embed.
4. **One store.** A pasted picture is stored at once (`POST /api/ai/pictures`,
   normalized, content-hash name) and the message keeps the URL. The chat
   rows shrink from megabytes of base64 to a URL each, and the upload GC
   counts the active and archived conversations as references
   (`upload_gc.referenced`), so a picture lives as long as a chat or a note
   names it and 30 days more. A picture the model copies from the chat into
   a note is the same file with one more reference. A separate chat-pictures
   table was considered and rejected: the upload URL is the one reference
   grammar, and keeping to it is what makes the GC, exports and quota agree.
5. **A way out.** Parametric, cached, workspace-scoped render routes
   (`GET /api/ai/page-image/{page_id}`, `GET /api/ai/ink-image/{block_id}`)
   show the user what the model looked at: a view chip expands to the
   picture, and the tool result names the URL so the model can embed it in
   its reply as a markdown image. Nothing is stored for them; the PDF never
   changes under its hash. `view_image` opens a note's own pictures;
   `clip_region` stores a region or a handwriting view as an upload the notes
   can embed, and a written block may only name uploads that exist.

Image generation was considered and left out: Anthropic has no image
output, OpenAI's needs a platform key and a separate model, and the pictures
a notes user wants are diagrams, tables and equations, which Mermaid, tables
and LaTeX already render in replies and notes.

## Still open

- A batch approval: several asking calls of one round still show one card
  each, so a reorganization of ten blocks is ten approvals.
- Transcribing every handwriting group on a page in one approval, and
  turning a group into a text block.
- Creating highlights, text boxes and sheets from the chat.
- A lightbox for a picture in a bubble; today a click does nothing.
- Compaction ([ai-context.md](ai-context.md) step 9): the window fit still
  drops whole turns, pictures and text alike.
