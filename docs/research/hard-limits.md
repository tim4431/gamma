# Hard limits a user can run into

An October 2026 survey of the fixed caps a normal user can reach: sizes,
counts, lengths, timeouts, rate limits, and lists cut at N with no way to
page. It covers the backend, the web client, the browser extension and the
desktop app; the account server and the website are left out. The
deliberate settings are named once and not listed again: the per-file
cap `max_upload_mb` (50 MB by default, at most 2048,
`server_settings.py`; dropped since, below), the storage quota
(unlimited by default, 200 MB for a guest) and the plans' caps.

## What changed with this survey

The survey ranked the caps below; the first seven were removed. How each
works now is in the dev docs named.

- **The import review's upload** (Zotero, Markdown, Gamma exports;
  [dev/import_export.md](../dev/import_export.md)). Staging refused an
  upload past 1 GB (`import_staging.MAX_BYTES`); it now has no size cap
  and refuses an upload only when it would leave the disk with less than
  1 GB free (507). The upload went as one request, which a server behind
  Cloudflare refuses past 100 MB; past 32 MiB it now goes in parts
  (`/api/import/review/parts`). A picked folder was zipped in the
  browser without ZIP64, so one past 4 GB or 65,535 files was refused;
  `transfers/folderZip.js` now writes ZIP64 records.
- **Restoring or merging a backup zip** went as one request; past 32 MiB
  it now goes in parts (`/api/jobs/restore/parts`), staged the same way
  ([dev/tasks.md](../dev/tasks.md)).
- **A mirror's or a publish's file pushes** went as one request, built
  whole in memory; past 32 MiB a file now goes in parts of 8 MiB over the
  remote's `/api/uploads/parts`, which takes any file as `kind: file`
  ([dev/mirror.md](../dev/mirror.md)).
- **Library search** fetched the server's top 20 notes and 15 PDF pages
  and filtered them by the folder and label chips in the browser, so
  matches inside the chosen folder went missing. The chips are now the
  server's `scope`, every group pages on with "Show more", and one paper
  gives at most 3 hits a round ([dev/api.md](../dev/api.md),
  `/block-search` and `/pdf-search`).
- **Reminders** kept 500 done marks, so older dismissed reminders came
  back as overdue, and read 500 candidate blocks a workspace in no order,
  so some never fired. Done marks are now a table (`reminders_done`,
  migration step 39) pruned only when a reminder is really gone, and every
  reminder is read ([dev/mentions.md](../dev/mentions.md)).
- **PDF passages handed to the AI** were cut at 4,000 characters each,
  6 passages and 24k in all, without a word. A message's passages now
  share the single-paper context budget (Settings → AI, 60,000 characters
  by default); a chip says before sending what will go, and the reply
  says what went ([dev/ai.md](../dev/ai.md)).
- **Markdown imports** dropped a document's blocks past 5,000 and a zip's
  notes past 2,000 without a usable way round, cut Notion databases at 500
  rows and refused zips past 1 GB uncompressed. A document now holds up to
  200,000 blocks and is refused whole past that, never cut; a zip has no
  note or size cap, with a zip-bomb ratio rule in place of the size; every
  database row is kept; a refused paste stays text and says so
  ([dev/import_export.md](../dev/import_export.md)).
- **PNGs in a PDF export** past 2.5 megapixels became the word "image".
  PNGs that copy as they are now embed at any size; the rest (alpha,
  16-bit, interlaced, GIF, WebP) decode with Pillow up to 40 MP, a 25 MP
  screenshot in about 0.65 s, keeping transparency as a soft mask
  ([dev/pdf_typesetting.md](../dev/pdf_typesetting.md)).

Later in October 2026 the per-file cap went too
([dev/user_db.md](../dev/user_db.md) "Storage limits"). The quota already
bounds what an account stores, and PDFs went up spooled to disk and in
parts, so the cap's one effect was to refuse scanned books and skip a
Zotero export's larger PDFs. `/upload-image`, `/upload-file` and the
Logseq import now spool to disk as `/uploads` does. A Notability note's
PDF or picture is read whole to be parsed and composed, so the importer
keeps a ceiling of its own on it (`MAX_HELD_BYTES`, 256 MB).

## Uploads still sent as one request

A body over 100 MB is refused by Cloudflare in front of a server; the
client names that refusal (`utils.proxyRefusal`).

| Path | Can it pass 100 MB | Notes |
|---|---|---|
| An attachment or a picture (`/upload-file`, `/upload-image`, `transfers/FileChip.jsx`) | From a share link's editors | A member's file past 32 MiB goes in parts (`/uploads/parts`); a share link's editors upload through `require_ws_writer`, in one request. The server spools it to disk |
| The Logseq import (`/import/logseq`) | Rarely | Plain `fetch`, so the status is lost from the message |

## Silent truncation and tight caps still there

Ranked by how likely a user meets them.

| What | Limit | Where | What the user sees |
|---|---|---|---|
| Backlinks ("Linked from") | 50, newest first | `routers/blocks.py` | Older links are missing |
| Chat history | 200 archived chats per page, folder or home | `routers/chats.py` | Older chats cannot be reached |
| Clipping a selection (extension) | 20,000 characters | `routers/clip.py` | The quote is cut, ten times below a block's own limit |
| Ink in one group | 5,000 strokes, 500k samples, 4 MB | `ink/ink.js`, `ink.py` | The oldest strokes drop; past 4 MB the save is retried every 2 s and the work is gone after a reload |
| Uploads in parts | 8 open per workspace, dropped after an hour idle | `upload_parts.py` | 429 for up to an hour after tabs closed mid-upload |
| AI calls at once | 6 per account | `ai_client.py` | Page translation (4 at once) beside an agent chat (4 at once) reaches it |
| PDFs the AI fetches or a tab hands off | 40 MB; 60 MB through the extension's in-page relay | `ai_web.py`, `routers/ai_handoffs.py`, `extension/detect.js` | Books and theses refused |
| The library as the AI lists it | 400 pages, no offset | `ai_tools.py` | "(+N more not shown)", with no way to page |
| Reading positions | 200 pages remembered | `App.jsx` | An older paper opens at page 1 |
| PDFs attached to a chat message | 15 MB each, 4 a message | `chat/ChatDock.jsx`, `ai_context.py` | A fifth is ignored without a word |
| SVG and Mermaid fences | 50,000 characters; 500 Mermaid edges | `shared/lib/svgSanitize.js`, `shared/lib/mermaidRenderer.js` | An exported matplotlib or draw.io SVG is refused |
| Sign-in and link-visitor rate limits | Per IP | `ratelimit.py` | Behind a proxy without `FORWARDED_ALLOW_IPS` every client shares one address |
| Folders on disk (desktop) | 10 per workspace | `folder_links.py` | "A workspace keeps at most 10 folders on disk." |
| The desktop's server start | 60 s | `desktop/lib/sidecar.js` | A long migration after an upgrade may pass it (not tested) |
| The search index | The first 20k characters of a block or a PDF page | `db.py`, `routers/search.py` | The AI's and MCP's search miss later text (Ctrl+F reads it all) |
| The Markdown zip report | The first 200 warnings | `markdown_zip_import.warn` | Later pages of a large vault lose their warnings and "missing" marks |

## Protective and sized well

200k characters a block, with a notice; 5 MB a Markdown file; 25 MB of
dictation and of an AI picture; 20 active jobs an account and 20 manual
snapshots a workspace; 50 workspaces a user; the block search's 2 s
budget, which says it stopped early and now goes on from where it
stopped; 32 AI tool rounds (up to 100) and 200 actions a message; 5,000
characters of a translated selection, with a message; 1 GB of free disk
before an export, a snapshot or a staged upload.

## Order worth fixing

1. Ink past 5,000 strokes or 4 MB: work is lost without a word.
2. Backlinks and chat history: page them instead of cutting at 50 and 200.
3. Clipping: the same 200,000 characters as a block.
4. A fifth chat PDF ignored, and the 15 MB per PDF: say so, or fall back
   to its text as larger PDFs already do.
5. The rest of the table as it comes up.
