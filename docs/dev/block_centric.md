# Block-centric Gamma (design + roadmap)

Direction stated 2026-09-02: Gamma is a Notion-like knowledge base. The block
is the unit, a page is a root block, and a page that carries a PDF *attachment*
opens as the conventional reading window. PDF reading is a feature of one kind
of attachment, not the organizing principle of the app.

This doc records the target model, the inventory of PDF-centric assumptions
that contradict it (as of 2026-09-02), and the staged plan to remove them.
Update it as stages land.

## Target model

**Page.** A block with `parent_id = 'root'`. Identity = block id. Title =
`content`. Every feature (open, share, export, search, AI context, links,
tabs, recents, snapshots, chats) keys on the page id and works on a page with
nothing but text.

**Attachment.** A file the page carries. Today there is exactly one slot,
`properties.doc_id` (content hash → `uploads/<doc_id>.pdf`) plus
`source_url` / `original_filename` / `web_url` describing where it came from.
This stays the storage shape for now, but code reads it through ONE helper on
each side — `page_attachment(props)` (backend) / `pageAttachment(block)`
(frontend) returning `{kind: "pdf", id, url, name} | null` — so that a later
`properties.attachments: [...]` list is a drop-in. Nothing else may read
`doc_id`/`source_url` off a page directly to decide *what the page is*.

**Reading window.** Opening a page whose attachment is a PDF shows the viewer
beside the notes; a page without one centers the notes. Same page component,
same dock, only the presence of a viewer differs — and the viewer is
collapsible (`pdfHidden`) rather than a mode. Layout derives from
`pageAttachment(page)`, never from a `pdfUrl` state variable.

**Kinds.** No `kind` column, no page-type enum. Cards, badges and AI listings
describe a page by what it carries ("has PDF", "has web source", labels),
not by a PDF/Note dichotomy.

**Highlights** stay child blocks with `highlight_id` / `pdf_position`; they
anchor to the page's PDF attachment. When multi-attachment arrives they gain
an `attachment_id` (= doc_id) — until then it is implicit.

**Sub-pages.** Not a new structure: the tree already nests arbitrarily and
`?block=<id>` opens any block on its page. "Open as page" = Logseq-style
zoom-in on a subtree (focus mode), not a second page table. The flat library
with folder labels stays the navigation model.

## Where the code still says "page = PDF" (inventory, 2026-09-02)

Backend
- `blocks_store.get_or_create_doc_page` + `GET/POST /blocks/by-doc/{doc_id}`
  are the only "open-or-create page" path; the lookup key is the PDF hash.
  Note pages are created by a bare `POST /blocks {parent_id:"root"}`.
- `clip.find_page` / `GET /library/lookup` scan only pages with `doc_id`; a
  web clip without a PDF lands in a hard-coded "Web clips" page.
- `search.py`: the only FTS index is `pdf_fts(doc_id, page, content)`;
  `/pdf-search` returns nothing for users without PDFs; there is no block FTS
  (`/block-search` is a Python scan).
- `ai_context.py`: context is framed as "Here is the PDF text"; a page with no
  `doc_id` contributes only via `include_notes`. `AIChatRequest` takes both
  `doc_id` and `pages`. `ai_tools.search_pdfs` searches PDF text only;
  `list_pages` tags pages `pdf`/`note`; `read_page` args are `pdf_*`.
  System prompts: "helping the user understand a PDF they are reading".
- `shares.doc_id` is `NOT NULL` and still returned to the client (vestigial
  since shares were re-keyed by page).
- `export-pdf` 400s without `doc_id` (correct — that format IS the PDF); the
  dialog already falls back to notes-as-PDF.
- `metadata/status` skips pages with neither `doc_id` nor `source_url`
  (now via `page_attachment()` — correct until stage 3 widens it).
- ~~`POST /uploads` rejects non-PDF; `upload-image` is the only other file
  path.~~ `POST /upload-file` takes the allowlist (stage 1).
- ~~`clip._default_title` and `metadata._save_props` still know the
  "PDF Notes - " auto-title prefix.~~ Gone (migrated, stage 0).

Frontend
- `App.jsx`: `homeMode = !pdfUrl && …`, `pageOnly = !pdfUrl && !!focusedBlockId`,
  `centerNotes`, `.main.pdfHidden` — mode is derived from `pdfUrl`.
- `pdfTitle` is really the page title (document.title, notes header, share
  topbar, chat chip, delete confirm) and falls back to "PDF Notes".
- Snapshot capture, scroll restore and read-position sync are gated on
  `pdfUrl`; note pages never get a recents cover.
- New page creation only lives in the "+" popover ("New note page"); the
  library grid has "New folder" but no "New page" tile; empty state says
  "open a PDF or start a note page".
- Cards: `kind = _sourceUrl ? "PDF" : "Note"`; tooltips say "paper"
  throughout; link chips say "linked paper".
- Chat dock page picker: `homeBlocks.filter(b => b.properties?.doc_id)` —
  note pages cannot be attached as context; copy says "Search your papers".
- Search panel groups "this paper" / "This PDF"; library hits only from
  `/pdf-search`.
- Settings nav group "Reading" (viewer / search / notes).
- Metadata popover shown only when `docId`, although `properties.meta` is a
  page property.

Already generalized (build on these): `?block=` deep link, `[[ref]]`,
`![[embed]]`, backlinks, `link_page_id`, page-keyed shares/chats/snapshots/
tabs, markdown-import and web-clip pages, notes-as-PDF export,
`/block-search`, orphan cleanup by textual `/api/uploads/` reference.

## One-time cleanup of old data shapes

Gamma has no migration framework: schemas are `CREATE TABLE IF NOT EXISTS`
on connect plus lazy `ALTER TABLE ... ADD COLUMN` (`db.py connect_users_db`).
Old rows were historically tolerated forever by read-side shims, which means
every renamed property or syntax lives twice in the code. The block-centric
work replaces that with ONE idempotent normalization pass, `gamma/migrate.py`,
run by `python manage.py migrate` and on every server start
(`app._startup_maintenance`). Each step only touches rows that still carry
the old shape (SQL-filtered), so a clean database costs one query per step.

Per-user `pages.db`
- `properties.sourceUrl` (camelCase, earliest pages) → `source_url`; the old
  key is removed.
- Legacy Logseq image size `![alt](url){:width N}` in block content →
  Obsidian `![alt|N](url)`.
- Pages still titled `PDF Notes - <name>` with no `auto_title` marker: title
  becomes `<name>` and `auto_title` is set to it, so the metadata worker may
  still replace it and the prefix special-case in `metadata._save_props` can
  go.
- Stage 4 (when it lands): highlight blocks under a page with `doc_id` gain
  `attachment_id = doc_id`.

Per-user `data.db`
- Drop the legacy `annotations` and per-user `shares` tables (superseded by
  `unified_blocks` and the global `shares` table long ago).

Global `users.db`
- Backfill `shares.page_id` for rows minted when shares were keyed by PDF
  (resolve through the owner's pages.db; rows whose document is gone are
  deleted — they could never resolve).
- Stage 3: drop `shares.doc_id` (rebuild the table; SQLite `DROP COLUMN`
  needs 3.35+). Only after the Docker image and desktop release both ship
  code that no longer writes it, since an older binary would fail to INSERT.

Status (2026-09-02): `gamma/migrate.py` is in place with every step above
except the stage-3/4 schema ones (`run_all()` at startup + `manage.py
migrate`, tests in `tests/test_migrate.py`), and the matching read-side shims
are deleted (`sourceUrl` fallbacks in `ai_context`/`metadata`/`pdf`, the
`PDF Notes - ` recogniser in `metadata._save_props`, `auth._legacy_share_page`
+ the lazy backfill in `share_lookup`). New code writes only the new shape.

## Roadmap

Each stage is independently shippable and leaves the app working. No schema
change until stage 4.

### Stage 0 — one source of truth for "what does this page carry"
*(frontend done 2026-09-02: `pageAttachment`/`pageKindLabel`/`defaultPageTitle`
in libraryUtils.js, `homeMode`/`pageOnly` derived from the focused page's
attachment, `pageTitle` state, "Untitled" fallback, copy sweep, Settings group
"Editor", chat picker lists every page.)*
- Add `page_attachment()` (`gamma/blocks_store.py`) and `pageAttachment()`
  (`frontend/src/libraryUtils.js`); route every `doc_id`/`source_url` read
  that decides layout, kind, gating or copy through them. — **done
  (backend)**: `page_attachment()` gates `metadata/status`, labels
  `ai_tools.list_pages` (`attachments=[pdf]` / `[]` instead of pdf/note) and
  the page endpoints; `clip.find_page` / `by-doc` are documented as
  lookups BY attachment and stay `doc_id`-keyed.
- Derive `homeMode` / `pageOnly` / `centerNotes` from
  `pageAttachment(currentPage)`; keep `pdfUrl` only as the viewer's input,
  set from the attachment. Rename `pdfTitle → pageTitle`, `docId` stays as
  the attachment id.
- Fallback title "Untitled" everywhere; retire the "PDF Notes - " prefix. —
  **done (backend)**: `clip._default_title` is the URL tail / doc id,
  `create_page` defaults to "Untitled", old rows were migrated (no
  recogniser left).
- Copy sweep: "paper" → "page" except where a PDF is genuinely meant
  (annotated-PDF export, import annotations, viewer settings). Settings nav
  "Reading" → "Editor" group with a "PDF viewer" pane inside it.

### Stage 1 — page-first creation, PDF as an action on a page
*(frontend done 2026-09-02: New page tile/row + `createPage()`, the page
header paperclip → `attachPdfToPage()` (URL/arXiv/DOI or upload; a PDF
dropped on an attachment-less open page attaches too), non-image files
dropped on a block upload via `/api/upload-file` and render as a `FileChip`,
text-preview covers, text-only pages remember their top block in the synced
read-position map as `{page: 0, block}`.)*
- `POST /api/pages {title?, folder?}` (thin wrapper, returns the page);
  `by-doc` stays as *lookup-by-attachment* for clip/dedup and PDF ingest,
  documented as such. — **done (backend)**: `routers/pages.py`,
  `blocks_store.create_page` (shared with `get_or_create_doc_page`).
- Home library: "New page" is the first tile/row (same in-place pattern as
  "New folder"), plus in the folder context menu; empty state offers it.
  Creating a page opens it with the title in edit.
- Page-level "Attach PDF" (upload / URL / arXiv / DOI) on a page without one:
  resolves via the existing `pdf.resolve_source` + `download_pdf` +
  metadata fetch, sets the attachment on THIS page (no new page). Uploading a
  PDF from home = create page + attach, one code path. — **done (backend)**:
  `POST/DELETE /api/pages/{id}/attachment` (409s for "already has one" and
  "another page carries this doc_id" with that page's id; automatic title +
  `auto_title` marker; detach sweeps the orphaned file).
- Generic file uploads: `POST /uploads` accepts an allowlist beyond PDF
  (md/txt/csv/json/office/zip …) and blocks reference them as
  `[name](/api/uploads/<hash>.<ext>)` chips (the image path already works
  this way, cleanup already follows textual references). Drag-drop a file
  onto the notes = upload + chip; drop a PDF onto a page without one =
  attach. — **done (backend)**: `POST /api/upload-file` (allowlist in
  `storage.FILE_MEDIA_TYPES` + images + pdf), `GET /uploads/{name}` serves
  them (inline only pdf/images/txt/md, the rest as attachments, html
  sandboxed); share reads and orphan cleanup already match textually.
- Cards and row badges describe attachments ("PDF" glyph badge when the page
  has one) instead of PDF/Note kinds; recents covers for text-only pages are
  a text preview (first lines) rendered by `PageCard`, no screenshot. —
  **done (backend)**: the root listing carries `preview` per page (first
  non-highlight child blocks, ~240 chars, one window query).
- Chat dock page picker lists ALL pages; copy "Search your pages…".

### Stage 2 — search and AI read the whole knowledge base
- Add `block_fts(block_id, page_id, content)` (FTS5, same `textnorm`
  normalization, maintained on block writes) next to `pdf_fts`; one
  `/api/search` returning hits with `source: "notes" | "pdf"`, page id, and
  for PDF hits the page number. `/pdf-search` and `/block-search` become
  thin wrappers, then are removed from the frontend.
- Search panel groups: titles → this page (notes, then its PDF) → other
  pages → PDF text; "This PDF" only when the open page has one.
- `AIChatRequest`: drop `doc_id`; `pages` + `page_id` only, attachment
  derived server-side. `ai_context.build_messages` frames context as
  "pages from the user's knowledge base"; a page section = title,
  properties, notes, and (if attached) PDF excerpts.
- Tools: `search_pdfs` → `search_library` (blocks + PDF text, `source`
  field); `list_pages` reports `attachments: ["pdf"]` instead of a kind;
  `read_page` args `pdf_page/pdf_chars/pdf_offset` keep working but are
  documented as "attachment text". System prompts rewritten around
  pages. Update `docs/dev/ai*.md` in the same change.

### Stage 3 — the page as a document
- Page header: title, labels, an "Attachments" row (PDF chip opens/toggles
  the viewer, web source chip, other files) and metadata (DOI/arXiv/authors
  from `properties.meta`) available on ANY page — a note about a paper you
  do not own the PDF of can still cite. Metadata popover no longer gated on
  `docId`; `metadata/status` covers pages with `meta` or `source_url` too.
- Extension clip without a PDF creates a page with `web_url` (title from the
  tab) instead of appending to "Web clips"; keep "clip selection into page"
  as the append path.
- Drop `shares.doc_id` (one-time backfill already keyed by page).
- Read-position / scroll restore for text-only pages (top block id), so
  reopening any page lands where you were.

### Stage 4 — beyond one PDF per page (optional, later)
- `properties.attachments: [{id, kind, name, source_url}]` with the primary
  PDF mirrored into `doc_id` for compatibility; highlights gain
  `attachment_id`; viewer gets an attachment switcher; search index keys on
  `(page_id, attachment_id, page)`.
- "Open as page" zoom-in on any block (breadcrumb back to the page), tabs
  and `?block=` already carry the id.

## Non-goals (for now)
- No `kind` column or page-type enum — describe pages by what they carry.
- No page-tree sidebar replacing folder labels; the library stays the
  navigation surface.
- No rewrite of the viewer; it remains the PDF attachment's renderer.
