# Hard limits a user can run into

An October 2026 survey of the fixed caps a normal user can reach: sizes,
counts, lengths, timeouts, rate limits, and lists cut at N with no way to
page. It covers the backend, the web client, the browser extension and the
desktop app; the account server and the website are left out. The
deliberate settings are named once and not listed again: the per-file
cap `max_upload_mb` (50 MB by default, at most 2048,
`server_settings.py`), the storage quota (unlimited by default, 200 MB
for a guest) and the plans' caps.

## What changed with this survey

The import review's upload (Zotero, Markdown, Gamma exports) lost its
three caps ([dev/import_export.md](../dev/import_export.md)):

- `import_staging.MAX_BYTES` refused a staged upload past 1 GB. Staging
  now has no size cap and refuses an upload only when it would leave the
  disk with less than 1 GB free (507).
- The upload went as one request, which a server behind Cloudflare
  refuses past 100 MB. Past 32 MiB it now goes in parts
  (`/api/import/review/parts`), the protocol PDFs already used.
- A picked or dropped folder was packed into a zip without ZIP64, so a
  folder past 4 GB or 65,535 files was refused in the browser.
  `transfers/folderZip.js` now writes ZIP64 records; Python's `zipfile`
  reads a 4 GiB entry and the entry after it.

What stays: each PDF inside a Zotero export passes the per-file cap like
any upload, so at the default 50 MB a larger PDF is skipped, with "file
too large (max 50 MB)" in the import report. An admin raises the cap in
Settings → Server.

## Uploads still sent as one request

A body over 100 MB is refused by Cloudflare in front of a server; the
client names that refusal (`utils.proxyRefusal`).

| Path | Can it pass 100 MB | Notes |
|---|---|---|
| Restoring or merging a backup zip (`tasks/useAppJobs.js` → `POST /api/jobs/restore`), and adding a shared page from its link, which goes through the same upload | Yes: a backup carries every PDF, and the server sets no cap | The main one left |
| A mirror or a publish pushing a file to its remote (`sync_engine.post_file`) | Yes | The whole file is built in memory, with a 600 s timeout; a refused file fails every round and is sent whole again |
| An attachment or a picture (`/upload-file`, `/upload-image`, `transfers/FileChip.jsx`) | Only after an admin raises `max_upload_mb` past 100 | The server reads it whole into memory |
| The Logseq import (`/import/logseq`) | Rarely | Plain `fetch`, so the status is lost from the message |

## Silent truncation and tight caps

Ranked by how likely a user meets them.

| What | Limit | Where | What the user sees |
|---|---|---|---|
| Library search (Ctrl+F) | 20 note hits, 15 PDF-page hits, 8 titles; the folder and label chips filter in the browser after the server's top N | `search/SearchPanel.jsx` | Matches inside the chosen folder are missing, or none show at all |
| PDF passages handed to the AI | 4,000 characters a passage, 6 passages, 24k in all; 12 block chips | `App.jsx`, `ai_context.py` | Nothing: the rest of a long selection never reaches the model |
| Markdown zip import | 2,000 notes a zip; 500 rows a Notion database; 50 nested zips; 1 GB uncompressed | `markdown_zip_import.py` | A warning; importing again meets the same first 2,000 |
| PNGs in a PDF export | 2.5 megapixels (`pdf_image.MAX_PIXELS`), checked before the path that copies a PNG as it is, which costs nothing | `pdf_image.py` | The picture becomes an italic "image"; a Retina screenshot is 3 to 5 MP |
| A Markdown document, and a paste turned into blocks | 5,000 blocks (`markdown_import.MAX_BLOCKS`) | `markdown_import.py`, `editor/BlockTree.jsx` | Nothing: the rest is dropped; a paste over 5 MB fails without a message |
| Backlinks ("Linked from") | 50, newest first | `routers/blocks.py` | Older links are missing |
| Chat history | 200 archived chats per page, folder or home | `routers/chats.py` | Older chats cannot be reached |
| Reminders | 500 done marks kept (`mentions.DONE_CAP`); 500 candidate blocks read per workspace, in no order | `mentions.py`, `app/reminders.js` | Past 500 done, the oldest come back as overdue; a new reminder may never fire |
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

## Protective and sized well

200k characters a block, with a notice; 5 MB a Markdown file; 25 MB of
dictation and of an AI picture; 20 active jobs an account and 20 manual
snapshots a workspace; 50 workspaces a user; the block search's 2 s
budget, which says it stopped early; 32 AI tool rounds (up to 100) and
200 actions a message; 5,000 characters of a translated selection, with
a message; 1 GB of free disk before an export, a snapshot or now a staged
import.

## Order worth fixing

1. The backup and restore zip in parts.
2. Mirror and publish pushes in parts.
3. Search scoped on the server, with paging.
4. The reminders' done cap, which brings dismissed reminders back.
5. Say when a selection for the AI is cut, and raise its 4,000 characters.
6. The PNG cap only for the PNGs that need the Python unfilter.
7. A warning at the 5,000-block and 2,000-note caps.
