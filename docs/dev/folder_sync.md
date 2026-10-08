# Folders on disk

A **folder link** keeps one folder of a workspace as a directory on a disk:
each paper as its PDF beside a Markdown file of its notes, the subfolders
as subdirectories, kept up to date from Gamma. It is one-way, Gamma to
disk: what Gamma holds is written out, what you change on disk is never
sent back and never overwritten either. Two hosts run the same rounds: the
**server** keeps links on its own disk (Settings → Workspaces → Folders on
disk; a NAS share then carries the directory to every PC), and the
**gamma-sync client**, one Python file, keeps a link on any computer over
HTTP. The design and the reasons, and the way back that is not built, are
in [research/folder-sync.md](../research/folder-sync.md).

Code: `gamma/folder_sync.py` (the layout, the manifest and the notes
files), `gamma/routers/sync.py` (`GET /api/sync/folders*`, beside the
change feed), `gamma/gamma_sync.py` (the rounds: the client that is also
the engine, the standard library only), `gamma/folder_links.py` (the
server's links: their store, the in-process source, the loop's tick),
`gamma/routers/folder_links.py` (`/api/folder-links*`),
`frontend/src/settings/SettingsFolderLinks.jsx` (the Settings section).
Tests: `backend/tests/test_folder_sync.py` (the reads and the client,
driven over the TestClient), `backend/tests/test_folder_links.py` (the
server's links), the browser suite's `folder-links` group
(`frontend/tests/e2e/scenarios/folderLinks.mjs`).

## What lands on disk

```text
<dest>/
  .gamma-sync.json          the link: server, folder, and every file written
  Sub/                      a folder below the linked one; empty ones too
    A paper.pdf             the paper, the file Gamma stores
    A paper.md              its notes and highlights
  A note.md                 a page without a PDF
  attachments/<hash>.png    the pictures the notes show, by content-hash name
```

The layout is the [Obsidian vault export](import_export.md#obsidian-vault-export)'s
with one difference: a paper's PDF sits next to its note instead of under
`attachments/`, so the directory reads as a folder of papers and still
opens as a vault (`source: "[[A paper.pdf]]"` resolves by name). The rules,
all on the server (`folder_sync.layout`):

- **A directory is a folder.** Every folder below the linked one becomes a
  directory, empty ones included, its name through `vault_name` (what
  Obsidian and Windows refuse is dropped; a Windows device name such as
  `CON` gets a trailing `_`). Two sibling folders whose names sanitize alike
  share a directory.
- **A page appears once**, under its first folder below the linked one in
  the page's own filing order, the rule every folder export uses
  (`_Filing.folder` in `routers/export.py`). Its other filings are not
  shown. Linking `root` takes the whole library: pages at the library root
  go to the top directory.
- **One stem per page**, `<dir>/<Title>`, unique in its directory ignoring
  case (`obsidian_export.unique_name`: ` 2`, ` 3`…), shared by the page's
  PDF and its notes file. The PDF is written only when the server stores it
  (a proxied PDF never saved has no file); the notes file always, so every
  paper has a place for its metadata. A link made for papers only writes
  no notes files.
- **The notes file** is the vault page (`render_vault_page`) rendered
  against the whole folder: a mention of a sibling page is `[[Title]]`
  (`[[dir/Title]]` when two share a name), a linked block carries its
  `^anchor`, highlights are `[!quote]` callouts linking the PDF's page,
  labels are `tags`, and the first front-matter line is `gamma_id: <page
  id>`, which is how a file keeps saying which page it is. Upload
  references are rewritten to `attachments/<name>` relative to the note's
  directory (`../attachments/` one level down). Ink, sheets and text boxes
  are not in the file, as in the vault export.

## The server's reads

Members read them, by a session or an integration token of either scope
(a read token is what the client is meant to use); share links do not.
All three are in `routers/sync.py`:

- `GET /api/sync/folders` — the folder tree as `{folders: [{id, path}]}`,
  for resolving a typed path and for the Settings dialog's choices.
- `GET /api/sync/folders/{id}` — the **manifest** (`folder_sync.manifest`):
  `{folder: {id, path}, cursor, dirs: [{id, path}], pages: [{id, title,
  stem, doc_id, pdf, pdf_size, notes, version}]}`. `pdf` and `notes` are
  the relative paths (`pdf` null when no file is stored), `doc_id` the
  PDF's identity, `version` the notes file's: the page's latest op seq and
  stamp plus a digest of its labels' names, so it changes whenever the
  file would. `cursor` is the change log's newest seq; `GET
  /sync/changes?since=<cursor>` lists nothing while the workspace is quiet
  ([collab.md](collab.md) "The change feed"). `root` is the library; 404
  for an id that is no folder.
- `GET /api/sync/folders/{id}/notes?pages=a,b,c` — the notes files of up
  to `MAX_NOTES` (200) pages as `{pages: {id: {markdown, attachments,
  version}}}`; pages outside the folder are left out. One context per
  request (the layout, the link texts, the anchors of linked blocks), so a
  client asks in batches rather than per page.

The PDFs and the attachments come from `GET /api/uploads/<name>` as for
any member ([user_db.md](user_db.md) "Stored files").

## A round

`gamma_sync.Round` is the engine both hosts run. It reads one folder
through a **source** with three methods — `manifest()`, `notes(ids)` and
`download(name, target)` — which `gamma_sync.RemoteSource` answers over
HTTP (the three reads above and `/api/uploads/`) and
`folder_links.LocalSource` answers in-process from the workspace. The
directory's **state file**, `.gamma-sync.json` (`gamma_sync.Link`), holds
the link (`server`, `workspace`, `folder`, `folder_path`, `notes`), the
last round's `cursor` and `synced_at`, the `dirs` it made, and `files`: per
relative path the page it belongs to, its identity (`doc` for a PDF,
`version` plus its `attachments` for a notes file, `attachment: true` for
a picture) and the `size` and `mtime` it had when written. That record is
what makes a round safe: it only ever writes, renames or removes paths it
has in `files`, and a file whose size or time no longer match counts as
changed on disk.

A round fetches the manifest and works through it in a fixed order:
renames first (a page's file at a new path, when the old file is still as
written and nothing is in the way, is moved rather than fetched again);
the directories; then each wanted file is compared with the state —
missing: fetched; same identity and present: unchanged; different
identity: fetched, unless the file changed on disk, which is *kept* and
reported; a file in the way that the round never wrote is kept too. Notes
are fetched in batches of 100, PDFs one by one into a `.part` beside the
target and moved into place once whole (a body shorter than the announced
length is an error, never a file). Then the attachments the notes name are
fetched once and the ones no note names any more are removed; files of
pages that left the folder are removed (a changed one is kept and
forgotten); directories of folders that are gone are removed when empty.
`full` writes every file again (the way to pick up a link text that lags
because another page's title changed), `force` replaces the files changed
on disk, `dry_run` reports and writes nothing. The round counts added,
updated, renamed, removed and kept, and lists the kept files with the
reason.

## Links kept by the server

A **link** is a row of users.db `folder_links` ([user_db.md](user_db.md);
migration step 35): the workspace, the folder (a folder block id, or
`root`), the directory's `path` below the **folders root**, whether notes
files are written, the change-log seq the last round saw (`cursor`) and
that round's `status`. The folders root is `GAMMA_FOLDERS_DIR`, else
`folders/` in the data directory (`config.folders_dir`); in Docker it is on
the data volume by default, and a bind mount plus the variable put it on a
share (`docker-compose.yml.example`). The path is cleaned (`clean_path`:
each name through `vault_name`, so nothing leaves the root), defaults to
the folder's own path, and is unique among the server's links ignoring
case. A workspace keeps at most ten links.

The directory is the round's: `_ensure_state` writes the state file on
creation (`server: "local"`) and adopts one an earlier link to the same
folder left behind — a directory holding another folder's files is
refused. Rounds run three ways: in the background when a link is made or
asked to sync, inline for a sync asked to wait, and from `tick`, which the
app's periodic loop runs every `TICK_S` (30 s): a link is *due* when its
workspace's change log moved past its cursor (so a quiet workspace costs
one query per link), when it has never synced, or `RETRY_S` (5 min) after
a failed round; a round marked running for over ten minutes counts as
dead. One round per link at a time (`_locks`). A link whose workspace is
gone is forgotten by the tick. Removing a link leaves the directory as it
is, or with `remove_files` takes back what the rounds wrote
(`_remove_written`: the files as recorded and unchanged, the directories
they made when empty, the state file, the directory when that leaves it
empty) and nothing else.

`/api/folder-links` ([api.md](api.md)) is session-only, never a guest, and
takes the editor role to make, change, sync or remove a link; any member
lists them. **Settings → Workspaces → Folders on disk**
(`SettingsFolderLinks.jsx`, [settings.md](settings.md)) shows the open
workspace's links as rows — the folder, the directory under the root, the
last round's outcome, a `kept` tag listing the files left alone — with
Sync, and a "more" menu: Write everything again, Replace files changed on
disk, Papers only / Papers and notes, Remove link, Remove link and files.
"Keep a folder on disk" picks the folder, names the directory and chooses
the files.

## The client

`gamma/gamma_sync.py` runs on its own: copy the one file to any computer
with Python 3.10 or newer and a token from Settings → Integrations (read
access is enough). `--help` on each command.

| Command | Does |
|---|---|
| `folders --server URL [--token T]` | lists the folders with their ids, and `root` |
| `init DEST --server URL --folder PATH\|ID\|root [--token T] [--save-token] [--no-notes]` | links the directory (made if missing): checks the token with `/sync/whoami`, resolves the folder by path (slashes between names, case and spaces ignored) or id, writes the state file |
| `sync DEST [--watch SECONDS] [--full] [--force] [--dry-run] [--token T]` | one round, or with `--watch` a round after every change, found by polling the change feed from the manifest's cursor |
| `status DEST` | what the directory is linked to and holds |

The token comes from `--token`, the `GAMMA_TOKEN` environment variable, or
the state file when `init --save-token` kept it there (then anyone with
the directory has it; a shared directory should not). `--watch` keeps the
process running: after a round it sleeps the interval, asks the change
feed whether anything moved past the cursor, and runs the next round only
then; errors are printed and retried at the next interval. Exit code 0
when the round ran (kept files are reported, not errors), 1 on a refused
token, an unreachable server or a missing link. The console is told to
replace characters it cannot show, so a title in another script never
stops a round on Windows.

## Limits

- One way. A change on disk stays on disk; the research note describes
  the way back.
- A link's text (`[[Title]]`) lags when the *other* page's title changes,
  until the linking page changes or a full round.
- Chats, reading positions and the trash do not travel, as for the mirror.
- The desktop app has no surface for this yet; on a PC the client is run
  by hand or by a scheduler, or the directory comes from the server's
  share.
