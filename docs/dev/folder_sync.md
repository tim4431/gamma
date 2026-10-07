# Folders on disk

A **folder link** keeps one folder of a workspace as a directory on a
computer: each paper as its PDF beside a Markdown file of its notes, the
subfolders as subdirectories, kept up to date from Gamma. It is one-way,
Gamma to disk: what Gamma holds is written out, what you change on disk is
never sent back and never overwritten either. The design and the reasons,
and the way back that is not built, are in
[research/folder-sync.md](../research/folder-sync.md).

Code: `gamma/folder_sync.py` (the layout, the manifest and the notes
files), `gamma/routers/sync.py` (`GET /api/sync/folders*`, beside the
change feed), `tools/gamma-sync/gamma_sync.py` (the client, one file, the
standard library only; its [README](../../tools/gamma-sync/README.md) is the
user's side). Tests: `backend/tests/test_folder_sync.py` drives the client
against the server in one process.

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
  paper has a place for its metadata. A link made with `--no-notes` writes
  PDFs only.
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
  for resolving a typed path.
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

## The client

`python gamma_sync.py <command>`; `--help` on each.

| Command | Does |
|---|---|
| `folders --server URL [--token T]` | lists the folders with their ids, and `root` |
| `init DEST --server URL --folder PATH\|ID\|root [--token T] [--save-token] [--no-notes]` | links the directory (made if missing): checks the token with `/sync/whoami`, resolves the folder by path (slashes between names, case and spaces ignored) or id, writes the state file |
| `sync DEST [--watch SECONDS] [--full] [--force] [--dry-run] [--token T]` | one round, or with `--watch` a round after every change, found by polling the change feed from the manifest's cursor |
| `status DEST` | what the directory is linked to and holds |

The token comes from `--token`, the `GAMMA_TOKEN` environment variable, or
the state file when `init --save-token` kept it there (then anyone with
the directory has it; a shared directory should not).

**The state file** `.gamma-sync.json` holds the link (`server`,
`workspace`, `folder`, `folder_path`, `notes`), the last round's `cursor`
and `synced_at`, the `dirs` it made, and `files`: per relative path the
page it belongs to, its identity (`doc` for a PDF, `version` plus its
`attachments` for a notes file, `attachment: true` for a picture) and the
`size` and `mtime` it had when written. That record is what makes the
client safe: it only ever writes, renames or removes paths it has in
`files`, and a file whose size or time no longer match counts as changed on
disk.

**A round** (`Round.run`) fetches the manifest and works through it in a
fixed order: renames first (a page's file at a new path, when the old file
is still as written and nothing is in the way, is moved rather than
fetched again); the directories; then each wanted file is compared with
the state — missing: fetched; same identity and present: unchanged;
different identity: fetched, unless the file changed on disk, which is
*kept* and reported; a file in the way that the client never wrote is kept
too. Notes are fetched in batches of 100, PDFs one by one into a `.part`
beside the target and moved into place once whole (a body shorter than the
announced length is an error, never a file). Then the attachments the
notes name are fetched once and the ones no note names any more are
removed; files of pages that left the folder are removed (a changed one is
kept and forgotten); directories of folders that are gone are removed when
empty. `--force` replaces the files changed on disk, `--full` writes every
file again (the way to pick up a link text that lags because another
page's title changed), `--dry-run` prints the plan and writes nothing,
not even the state. A summary line counts added, updated, renamed, removed
and kept.

`--watch` keeps the process running: after a round it sleeps the interval,
asks the change feed whether anything moved past the cursor, and runs the
next round only then. Errors in watch mode are printed and retried at the
next interval. Exit code 0 when the round ran (kept files are reported,
not errors), 1 on a refused token, an unreachable server, a missing link.

## Limits

- One way. A change on disk stays on disk; the research note describes
  the way back.
- A link's text (`[[Title]]`) lags when the *other* page's title changes,
  until the linking page changes or `--full`.
- Chats, reading positions and the trash do not travel, as for the mirror.
- The desktop app and Settings have no surface for this yet; the client is
  run by hand or by a scheduler.
