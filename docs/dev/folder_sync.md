# Folders on disk

A **folder link** keeps one folder of a workspace as a directory on a disk:
each paper as its PDF beside a Markdown file of its notes, the subfolders
as subdirectories, kept up to date from Gamma. It is one-way, Gamma to
disk: what Gamma holds is written out, what you change on disk is never
sent back and never overwritten either. Two hosts run the same rounds:

- The **desktop app** keeps folders on the computer it runs on, through
  its own local server. A folder comes from one of that server's
  workspaces, or from another Gamma server such as a NAS, read with a
  token of it and no clone. No other server keeps folders on disk; a NAS
  only answers the reads.
- The **gamma-sync client**, one Python file, keeps a link on any computer
  over HTTP.

The design, its reasons, and the way back that is not built are in
[research/folder-sync.md](../research/folder-sync.md).

Code:

- `gamma/folder_sync.py` — the layout, the manifest and the notes files.
- `gamma/routers/sync.py` — `GET /api/sync/folders*`, beside the change
  feed.
- `gamma/gamma_sync.py` — the rounds: the client that is also the engine,
  the standard library only.
- `gamma/folder_links.py` — the desktop server's links: their store, the
  in-process source, the remote source's token and poll, the loop's tick.
- `gamma/routers/folder_links.py` — `/api/folder-links*`.
- `desktop/main.js` — the desktop app's flows, with the bar's sync panel in
  `desktop/ui/bar.html` ([desktop
  architecture](../../desktop/docs/architecture.md) "Folders on this
  computer").
- Tests: `backend/tests/test_folder_sync.py` (the reads and the client,
  driven over the TestClient), `backend/tests/test_folder_links.py` (the
  links), and the desktop suite's folder steps (`desktop/test/e2e.js`).

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
  (`obsidian_export.Filing.folder`). Its other filings are not
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
  for resolving a typed path (the client's `--folder`) and for the desktop
  app's folder chooser.
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
`folder_links.LocalSource` answers in-process from the workspace.

The directory's **state file**, `.gamma-sync.json` (`gamma_sync.Link`),
holds the link (`server`, `workspace`, `folder`, `folder_path`, `notes`),
the last round's `cursor` and `synced_at`, and the `dirs` it made. Its
`files` map records, per relative path, the page the file belongs to, its
identity (`doc` for a PDF, `version` plus its `attachments` for a notes
file, `attachment: true` for a picture) and the `size` and `mtime` it had
when written. That record is what makes a round safe: it only ever writes,
renames or removes paths it has in `files`, and a file whose size or time
no longer match counts as changed on disk.

A round fetches the manifest and works through it in a fixed order:

1. Renames: a page's file at a new path is moved rather than fetched
   again, when the old file is still as written and nothing is in the way.
2. The directories.
3. Each wanted file, against the state. Missing: fetched. Same identity
   and present: unchanged. New identity: fetched, unless the file changed
   on disk, which is *kept* and reported. A file in the way that the round
   never wrote is kept too.
4. The attachments the notes name, fetched once; the ones no note names
   any more are removed.
5. The files of pages that left the folder are removed; a changed one is
   kept and forgotten.
6. The directories of folders that are gone are removed when empty.

Notes are fetched in batches of 100. PDFs come one by one into a `.part`
beside the target and are moved into place once whole; a body shorter than
the announced length is an error, never a file. `full` writes every file
again (the way to pick up a link text that lags because another page's
title changed), `force` replaces the files changed on disk, and `dry_run`
reports and writes nothing. The round counts added, updated, renamed,
removed and kept, and lists the kept files with the reason.

## Folders kept by the desktop app

Only the desktop app's own local server keeps folder links. The shell
starts it with `GAMMA_FOLDER_LINKS` (`config.folder_links_enabled`). On
any other server, a NAS among them,
`/api/folder-links*` answers 404 with a pointer to the desktop app, and
`tick` does nothing; a row left from an earlier build stays as it is.
Such a server only answers the reads above, for a link kept elsewhere.

A **link** is a row of users.db `folder_links` ([user_db.md](user_db.md);
migration steps 35 and 36). It holds the workspace, the folder (a folder
block id, or `root`), the directory's full `path`,
whether notes files are written, the change-log seq the last round saw
(`cursor`) and that round's `status`. A link whose folder is on another
Gamma server also holds that server (`remote_url`), a token of it
(`token`, Fernet-encrypted with the data directory's key like the mirrors'
tokens) and the token's id (`token_id`, for whoever revokes it there).

The path is the one the user picked, as the machine resolves it
(`clean_path`). A relative path, a filesystem root, and anything inside
the data directory are refused. The path is unique among the links
ignoring case. A workspace keeps at most ten links, local or remote,
counted by the workspace the folder belongs to.

**A link with a remote source** (`create_remote_link`) reads its folder
from another Gamma server with the client's own `RemoteSource`. The server
then runs for that folder what `gamma_sync.py` would run on a PC:

- `workspace_id` is the workspace on the other server, and the state file
  says `server: <url>`.
- The rounds go over HTTP: the three reads above and `/api/uploads/`. Only
  the folder's files come down, never the rest of the workspace.
- The token is checked on creation. `/api/sync/whoami` names its
  workspace, which must match the request's `workspace` when one is given,
  and the folder must exist there. Read scope is enough; the token is
  never answered back. The check waits at most `ASK_TIMEOUT_S` (15 s) on
  a silent server.
- The link belongs to the account that made it, not to a workspace. Only
  that account lists, changes and removes it.
- The token expires as its issuer set it (the desktop app mints a year).
  A refused token is the round's error; making the link again with a new
  token adopts the directory.
- Removing the link leaves the token on the other server. Whoever minted
  it revokes it, as the desktop app does.

The directory is the round's. `_ensure_state` writes the state file on
creation (`server: "local"`, or the remote's address). It adopts one that
an earlier link to the same folder of the same server left behind, and
refuses a directory holding another folder's files.

Rounds run three ways: in the background when a link is made or asked to
sync, inline for a sync asked to wait, and from `tick`. The app's periodic
loop runs `tick` every `TICK_S` (30 s), and a link is *due* when:

- its source moved past its cursor. For a local workspace that is its
  change log, read directly, so a quiet workspace costs one query per
  link. For a remote source it is the other server's change feed
  (`gamma_sync.changed_since`, `GET /sync/changes?since=<cursor>&limit=1`),
  asked at most every `REMOTE_POLL_S` (60 s) and waited on at most
  `ASK_TIMEOUT_S` (15 s). A server that cannot be
  reached or refuses the token counts as a failed round.
- it has never synced.
- its last round failed `RETRY_S` (5 min) ago or longer.

The tick runs a due link of this server's workspace itself, one after
another. It hands a remote link's round to a thread of its own
(`run_in_background`), whose `running` mark keeps the next tick off that
link, so a slow or silent server never holds up the rest. A round's own
requests may wait two minutes each (`gamma_sync.Server.timeout`), long
enough for a large PDF.

A round marked running for over ten minutes counts as dead. One round runs
per link at a time (`_locks`). The tick forgets a link whose workspace on
this server is gone. Removing a link leaves the directory as it is. With
`remove_files` it takes back what the rounds wrote and nothing else
(`_remove_written`): the files as recorded and unchanged, the directories
they made when empty, the state file, and the directory when that leaves
it empty.

`/api/folder-links` ([api.md](api.md)) is session-only and never a guest's.
A link of this server's workspace takes the editor role to make, change,
sync or remove; any member lists them. A link with a remote source is its
account's alone. The desktop app is the only caller. It makes, syncs and
stops links from its bar's sync panel and from a folder's *Keep on this
computer…* in Gamma's own menu ([desktop
architecture](../../desktop/docs/architecture.md) "Folders on this
computer"). Gamma's Settings has no section for them.

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
- Only the desktop app keeps folders on disk ("Folders kept by the desktop
  app" above); a computer without the app runs the client.
- A remote source's token expires as its issuer set; see the same section.
