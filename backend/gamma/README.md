# gamma/

The backend package. `app.py` assembles the middleware + routers and serves the built frontend.

```
# Configuration, storage and upgrades
config.py          env config
server_settings.py storage limits admins edit at runtime, and their enforcement
db.py              schemas, SCHEMA_VERSION, per-workspace DB paths, the connect helpers (closed by their with block), account id ↔ name, user prefs
db_maintenance.py  SQLite upkeep on the app's every() loop: idle connections closed, a grown WAL truncated, PRAGMA optimize
migrations.py      versioned data-directory upgrades (snapshot, numbered steps)
backups.py         server backups: snapshots under backups/ (create, zip, delete, restore)
backup_schedule.py named backup tasks: UTC cron, per-task retention, OS locking
offsite.py         off-site copies of the databases and uploads in an S3-compatible bucket (s3.py)
ws_backup.py       one workspace's backup zip: export, restore, stored snapshots, a deleted workspace's final copy
integrity.py       PRAGMA quick_check of a database; the latest failure per file (the db-damage notice)
normalize.py       content normalization of a workspace's files (migration + restore)

# Accounts, workspaces and sign-in
workspaces.py      workspaces + memberships (roles, billing, personal workspace)
auth.py            session middleware → request.state.user_id / user (read in a worker thread); request → workspace / share
guests.py          guest accounts: throwaway accounts that keep nothing (docs/dev/guests.md)
cloud_auth.py      Sign in with Gamma Cloud: an OpenID Connect client of cloud/, a verified identity → a session
shares.py          a share's invitations by Gamma Cloud username, and the Inbox item a share sends (docs/dev/cloud_accounts.md)
join_requests.py   Gamma Cloud accounts asking to join under the `invited` policy (docs/dev/cloud_accounts.md "Requests to join")
cloud_sync.py      what a linked account's cloud grant does: the hourly grant check, the profile sync, the server list
email_invites.py   invitations by e-mail to this server, a workspace or a share: mailed by the account server, claimed by the sign-in through the link
hosted.py          a hosted container (GAMMA_HOSTED=1) learns its plan's limits from the account server
integrations.py    revocable read / write tokens per account and workspace (MCP, the HTTP API, mirrors)
seed.py            workspace file creation, the Welcome page and Sketchbook new accounts start with, first admin
sketchbook.py      the seeded Sketchbook notebook, its cartoon sheets drawn as code (docs/dev/onboarding.md)
onboarding/        welcome.md, the seeded Welcome page (docs/dev/onboarding.md)

# Blocks, collaboration and sync
blocks_store.py    block tree helpers: subtrees, the change log (touch_page), the folder and label trees
ops.py             block operations: THE write path for a page's blocks
textmerge.py       three-way merge of one block's text (ops.py `set` with `base`)
collab.py          live rooms: who is on a page, the fan-out of applied operations
comments.py        comments: threads on a note, a page or a highlight, following their blocks (docs/dev/comments.md)
inbox.py           each account's Inbox: mentions, replies, resolves, shares, invitations and requests to join, settled when read (docs/dev/comments.md)
sync_tree.py       a page's tree as a flat snapshot, and the ops between two (the mirror's pure half)
sync_engine.py     mirrors: a local workspace kept in step with one on another Gamma server (docs/dev/mirror.md)
publish.py         publishing a page to the free share host (docs/dev/mirror.md "Publishing")

# Annotations, files and search
highlights.py      a block's place on a PDF page: the pdf_position shape (highlights, link regions, ink)
text_box.py        text boxes: typed text on a PDF page or a sheet of paper (docs/dev/text_boxes.md)
storage.py         uploads (content-addressed, written atomically) + the upload-reference grammar
upload_parts.py    a PDF's upload in parts: the sessions, the parts appended and hashed, stored by a rename
upload_gc.py       unreferenced uploads: recorded, kept 30 days, then purged (background thread)
trash.py           Recently deleted: the trashed pages, their 30-day purge (background sweeper)
ink.py             the gamma-ink stroke file: schema, codec, geometry, renderers, the three-way stroke merge
ink_view.py        handwriting as a picture for a vision model (the view_ink tool)
notebook.py        notebook pages: paper rules, sheets, the notebook as a PDF (docs/dev/notebooks.md)
recording.py       audio recordings: the recording block's rules, its label and export line, the transcribe job
block_index.py     the notes index (block_fts in pages.db, kept by triggers): its queries
pdf_index.py       the PDF text index (pdf_fts in data.db): schema, writes, queries
pdf_meta.py        a PDF's manifest for the viewer: bytes, page count, page sizes (data.db)
pdf_text.py        PDF text extraction (PyMuPDF, PyPDF2 fallback)
textnorm.py        search text normalization and fuzzy matching (both indexes, block search)

# Jobs, notices and diagnostics
jobs.py            background jobs: exports, backups, restores, imports, the search indexer (docs/dev/tasks.md)
notices.py         the notices behind the red dot on the account button
mentions.py        @ mentions in note text, and each account's reminders found through the notes index (docs/dev/mentions.md)
logbuf.py          in-memory server log (Settings → Diagnostics → Server log)
version.py         which build this server is, and whether a newer one exists
json_response.py   OrjsonResponse: large JSON answers encoded in the handler's worker thread

# AI: providers, the agent and its reach
ai_protocols/      one adapter per AI wire protocol (request, stream, usage, models, quota)
ai_client.py       provider-agnostic AI transport (open, read, stream, errors)
ai_catalog.py      live model listings + context windows (provider, then models.dev)
ai_settings.py     AI provider entries (GUI-managed keys): each account's own, the server's shared ones
chatgpt_oauth.py   ChatGPT subscription sign-in (OAuth PKCE, the flow Codex CLI uses)
ai_usage.py        token usage per AI call (users.db ai_usage) and the summary Settings → AI shows
ai_context.py      PDF attachments, extraction, and chat context assembly
ai_pictures.py     the chat's pictures: one size, the store, the budget, the label lines
chat_export.py     a chat conversation as Markdown with its pictures (docs/dev/chat_history.md)
ai_tools.py        the agent's tools: one scope-agnostic registry
ai_agent.py        the agent tool loop, its two user-facing pauses, and the helper a tool delegates to
ai_permissions.py  the chat tools' Allow / Ask / Off states and the approvals a reply waits on
ai_revert.py       taking back one change the agent made to the notes, or a folder or label it deleted, and putting it back
ai_web.py          the agent's scholarly reach: search_papers, related_papers, fetch_paper
openalex.py        OpenAlex: a registry search_papers asks, the citation graph behind related_papers
search_services.py the services behind the agent's web search (search_web)
paper_links.py     the links on a web page that may lead to a paper's full text, ranked
fetch_handoff.py   fetches handed to the user's browser when a paper is behind a wall
publisher_sessions.py  publisher cookies the Connector imports, stored encrypted
translate_engines.py  machine translation for the PDF's translated view (Microsoft, Google, Youdao)

# Assistants (MCP)
mcp_server.py      the MCP endpoint over the chat's tools, plus pasted links (mcp_links) and page files (mcp_export)
mcp_oauth*.py      browser consent for MCP clients; mcp_lazy.py loads the optional SDK on first use

# Import and export
logseq_*.py        Logseq: PDF-highlight importers (EDN / Markdown), a page as a Logseq file graph
markdown_*.py      Markdown import (one document, or a zip: Notion, Obsidian, Gamma's own) and export
zotero_*.py        Zotero RDF library import and export
notability*.py     Notability notes: the plists and the .ntb's FlatBuffers log read behind guards, a note's model, its place in Gamma, the import and the export
goodnotes*.py      GoodNotes notebooks: protobuf, Apple LZ4 and tpl read behind guards, a notebook's model, its place in Gamma, the import
note_app_import.py what the Notability and GoodNotes imports share: the run over an upload, the commit, the storing of a note's files
rtf.py             the RTF Cocoa writes (a GoodNotes text box), read as spans
obsidian_export.py an Obsidian vault export
import_*.py        the reviewed library imports: staged uploads, preview and selection, the guarded zip walk
bibtex.py          BibTeX entries and bibliographies, every printed entry valid BibTeX
item_types.py      what a paper record describes (meta.kind), in Zotero's item-type names
mathml.py          MathML in registry titles, as LaTeX math or as plain text

# PDF writers
pdf_export.py      highlights burned into a PDF as standard annotations
pdf_notes.py       the annotated PDF's notes and comment threads as cards, beside each page or on it
pdf_cards.py       one card: a highlight's notes or a comment thread, typeset from Markdown
pdf_free_space.py  where a PDF page is empty, and the nearest free spot for a card there
pdf_document.py    a note page typeset as a fresh PDF ("Notes as PDF")
pdf_typeset.py     the typesetting engine both PDF writers draw with (+ pdf_glyphs, vector_text, pdf_image)
note_markup.py     the span shape the writers draw, and LaTeX approximated as text when it cannot be typeset

# HTTP plumbing and assembly
net_guard.py       outbound-fetch safety: no internal, loopback or metadata hosts, no non-HTTP schemes
ratelimit.py       in-process fixed-window limits per key (bounded; the client address is the peer)
compression.py     gzip for whole JSON answers only (never streams, files or ranges)
app.py             assembly + SPA serving
routers/           one module per API area — see routers/README.md
```

## Data model — everything is a block

Each workspace's `pages.db` has the `unified_blocks` table (plus the `page_ops`
and `page_changes` logs, the AI chats and the notes index — docs/dev/user_db.md).
Rows form a tree via `parent_id`;
sibling order is the lexicographic `position` (fractional-index strings like `a0`, `a0V`).

```
parent_id = 'root'                         ← pages
   │  props.doc_id  → PDF page
   │  props.folders = [folder ids]         → filed in those folders
   │  props.labels = [label ids]           → its labels
   │
   ├─ block  (plain text)                  ← free note
   ├─ block  props.pdf_position            ← highlight on the PDF (its id is the block id)
   │     props.link_url / link_page_id         → clickable reference link
   │     (+ link_block_id: a highlight there)
   └─ block  …nested children (indent/outdent)

parent_id = 'trash'                        ← pages in Recently deleted (30 days;
                                             props.deleted_at / deleted_by) — no page
                                             until restored (trash.py)

parent_id = 'folders' (or a folder)        ← folders: content = name, nested
parent_id = 'labels'                       ← labels: content = name, flat
                                             (pseudo-pages to the op path;
                                             docs/dev/home_library.md)
```

Key columns: `id, parent_id, position, content, properties (JSON), created_at, updated_at`, the stored `page_id`, and the generated `kind` and `doc_id` (docs/dev/user_db.md).

Invariants:
- Positions come from `generate_key_between` — never hand-write them. The one
  exception is the reserved parentless rows, written with fixed positions
  because nothing orders them: `root` (`'a0'`, `seed.create_workspace_files`)
  and `trash`, `folders`, `labels` (`'a1'`, `ensure_reserved`).
- `PUT /blocks/{id}/children` replaces the whole subtree (delete + reinsert, one
  transaction; never `root`'s); the upload names it drops go to the orphan check.
- A file nothing references is kept 30 days, then purged
  (`upload_gc.py`, docs/dev/user_db.md "Stored files").
- Timestamps are UTC ISO strings with a `Z` suffix (`page_now()`).
