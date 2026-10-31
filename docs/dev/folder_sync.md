# Folders and files on disk

A **link to disk** pairs something in Gamma with something on a disk and
says which way bytes move:

| Link | In Gamma | On disk | Gamma to disk | Disk to Gamma |
|---|---|---|---|---|
| folder link | a folder, or `root` | a directory | always | PDFs, when asked (`push`) |
| file link | one page | one `.pdf` file | never | always |

A **folder link** keeps one folder of a workspace as a directory: each
paper as its PDF beside a Markdown file of its notes, the subfolders as
subdirectories, kept up to date from Gamma. What you change on disk is
never overwritten. It goes back only when the link sends PDFs back: a PDF
changed in the directory then becomes its page's new file, as *Replace the
PDF…* makes it. A **file link** makes one PDF on disk a page's file each
time the file changes, a manuscript compiled again from its TeX say, and
never writes the file. Notes files never go back. Two hosts run the same
rounds:

- The **desktop app** keeps folders and files on the computer it runs on,
  through its own local server. A folder or a page comes from one of that
  server's workspaces, or from another Gamma server such as a NAS, reached
  with a token of it and no clone. No other server keeps links to disk; a
  NAS only answers the reads, the uploads and the replacement.
- The **gamma-sync client**, one Python file, keeps a folder link on any
  computer over HTTP, and its `push` command sends one file.

The design and its reasons are in
[research/folder-sync.md](../research/folder-sync.md) (Gamma to disk) and
[research/disk-to-gamma.md](../research/disk-to-gamma.md) (PDFs back to
Gamma); the way back for notes files is not built.

Code:

- `gamma/folder_sync.py` — the layout, the manifest and the notes files.
- `gamma/routers/sync.py` — `GET /api/sync/folders*`, beside the change
  feed.
- `gamma/gamma_sync.py` — the rounds and the push: the client that is
  also the engine, the standard library only.
- `gamma/folder_links.py` — the desktop server's links: their store, the
  in-process source and sink, the remote source's token and poll, the
  loop's tick.
- `gamma/routers/folder_links.py` — `/api/folder-links*`.
- `gamma/routers/pages.py` `replace_attachment` — the replacement a push
  makes, shared with `PUT /api/pages/{id}/attachment`.
- `desktop/main.js` — the desktop app's flows, with the bar's sync panel in
  `desktop/ui/bar.html` ([desktop
  architecture](../../desktop/docs/architecture.md) "Folders and files on
  this computer").
- Tests: `backend/tests/test_folder_sync.py` (the reads and the client,
  driven over the TestClient), `backend/tests/test_folder_links.py` (the
  links), `backend/tests/test_disk_push.py` (PDFs sent back, file links),
  `backend/tests/test_upload_retention.py` (the files a replacement leaves),
  and the desktop suite's folder and file steps (`desktop/test/e2e.js`).

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
any member ([user_db.md](user_db.md) "Stored files"). Sending a PDF back
takes three more calls, each an editor's and so a write-scope token's:
`POST /api/uploads` (the file, one multipart part), `PUT
/api/pages/{id}/attachment` with `keep_old_days`, and `GET /api/blocks/{id}`
for what the page shows now ([api.md](api.md)).

## A round

`gamma_sync.Round` is the engine both hosts run. It reads one folder
through a **source** with three methods — `manifest()`, `notes(ids)` and
`download(name, target)` — which `gamma_sync.RemoteSource` answers over
HTTP (the three reads above and `/api/uploads/`) and
`folder_links.LocalSource` answers in-process from the workspace. A link
that sends PDFs back also writes through a **sink** ("Sending PDFs back"
below).

The directory's **state file**, `.gamma-sync.json` (`gamma_sync.Link`),
holds the link (`server`, `workspace`, `folder`, `folder_path`, `notes`,
`push`), the last round's `cursor` and `synced_at`, and the `dirs` it made.
Its `files` map records, per relative path, the page the file belongs to,
its identity (`doc` for a PDF, `version` plus its `attachments` for a notes
file, `attachment: true` for a picture) and the `size` and `mtime` it had
when written. That record is what makes a round safe: it only ever writes,
renames or removes paths it has in `files`, and a file whose size or time
no longer match counts as changed on disk.

With `push` on and a sink given, a round first sends the PDFs changed on
disk (`Push`, below), before it fetches the manifest, so the manifest
already names their new identity and the pull leaves them alone. Then it
fetches the manifest and works through it in a fixed order:

1. Renames: a page's file at a new path is moved rather than fetched
   again, when the old file is still as written and nothing is in the way.
2. The directories.
3. Each wanted file, against the state. Missing: fetched. Same identity
   and present: unchanged. New identity: fetched, unless the file changed
   on disk, which is *kept* and reported ("changed on disk", or for a PDF
   of a link that sends back, "changed here and in Gamma"). A file in the
   way that the round never wrote is kept too.
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
removed and kept (and, with push, sent and waiting), and lists the kept
files with the reason.

## Sending PDFs back

`gamma_sync.Push` makes PDFs changed on disk their pages' new files: one
pass over items, each a file and its **entry**, what was last known of it
(`{page, doc, size, mtime}`). A folder link's entries are its state file's
PDF entries; a file link's one entry is the row's `state` (below), or for
the client's `push` command, held in memory. The pass brings the entries
up to date in place and counts `sent`, `waiting` and `kept`.

It writes through a **sink** with three methods: `store(path)` (the file's
bytes stored, its `doc_id` back; known bytes are stored once),
`replace(page_id, doc_id, filename)` and `current(page_id)` (`{doc_id,
filename, title}` of the page now, None when the page is gone, in the
trash, or no page). `gamma_sync.RemoteSink` answers over HTTP with the
three calls above. `folder_links.LocalSink` answers in-process as the
account that made the link: `storage.store_pdf_stream` from the open file
(spooled, hashed, the quota and the `%PDF` header checked as for an
upload) and `pages.replace_attachment`. Before each write it asks whether
that account is still an editor of the workspace ("you may no longer edit
this workspace" otherwise).

**Identity decides.** A stored PDF's `doc_id` is the first 24 hex
characters of its SHA-256, so `gamma_sync.doc_of` hashes a file and knows
without a byte sent whether it is Gamma's file. A file is looked at only
when its size or mtime differ from the entry's (`modified`). Then:

- Not **settled** yet (`settled`): written less than `SETTLE_S` (3 s) ago,
  not open for reading (Windows refuses while its writer holds it), or
  without `%%EOF` in its last kilobyte. The file *waits*, with the reason
  ("still being written", "not a whole PDF yet"), and the next pass asks
  again. A compile writes its PDF over a second or more; an intermediate
  build that does settle is sent and replaced by the next. A file at rest
  that still does not end as a PDF ends (a download cut short, another
  file saved over it) also records its stat as `seen` (below), so it
  starts no round until it is written again, which moves its size or
  time. A file still being written or locked does not: a lock can end
  with no new stat, so the loop asks again at every tick.
- The same bytes as the entry's `doc` (touched, not changed), or the bytes
  the page shows already: the entry takes the file's stat, nothing travels.
- The page gone: *kept*, "the page is no longer in Gamma".
- In a folder, a page Gamma gave another file meanwhile (its `doc_id` is
  neither the entry's nor the file's): changed here and in Gamma. The push
  leaves it to the pull, which keeps the disk's file and says so. `force`
  takes Gamma's, and the page's *Replace the PDF…* with the disk's file
  lets the disk win. A file link has no such case: the disk wins. A file
  replaced in Gamma by hand stays until the file changes again, since only
  a changed file is looked at.
- Otherwise the file is **sent**: stored, then the page's PDF replaced by
  it under the page's own `original_filename`, or the file's name when the
  page has none. A file whose bytes or stat changed while it was stored
  waits for the next pass instead, without `seen`, and so does one whose
  store failed because the file moved under the read: one that shrank
  while it was sent over HTTP (`gamma_sync.Changed`), or any failure of
  the store with a new stat. A failure with the file standing still is
  the server's (below).

A failure is the file's, never the pass's. A refused token, a full quota,
bytes another page carries (the replacement's 409) and a page that is gone
make the file *kept* with the server's reason, and so does an answer
that is no JSON (a sign-in or captive portal's page) or one cut short;
the entry records the file's stat as `seen`. `moved`, the test the loop
and the client's `--watch` use, compares against `seen` when there is
one, so such a file does not make a round run again until it changes.
`Push` itself compares with the entry, not `seen`: a round that runs for
another reason (a change in Gamma, a sync asked for, a file link's retry
after `RETRY_S`) looks at the kept file again and sends it again when it
is whole. A missing file is no failure: in a folder it is the pull's to
fetch again, and a file link waits for it ("the file is not there").

**The replacement** is the one `PUT /api/pages/{id}/attachment` makes
([api.md](api.md)): the page keeps its id, notes and highlights, the
highlights are stamped with the file they were measured on, the
relocation run starts ([highlight_relocation.md](highlight_relocation.md)),
and the open viewers switch. A push asks `keep_old_days` of 1
(`gamma_sync.KEEP_OLD_DAYS`): the file the page let go of is purged a day
after nothing references it instead of thirty
([user_db.md](user_db.md) "Stored files"). A pending highlight measured on
that file is a reference, so the file stays as long as the highlight's
review needs it, and it goes a day after the last of them is resolved
(the last Accept). A file another page still carries or shows (a
duplicated page that shows it by its address) is no build let go of: it
keeps its thirty days. A run that cannot start reports `relocation_error`,
and the replacement stands.

## Folders kept by the desktop app

Only the desktop app's own local server keeps links to disk, folders
and files alike. The shell starts it with `GAMMA_FOLDER_LINKS`
(`config.folder_links_enabled`). On any other server, a NAS among them,
`/api/folder-links*` answers 404 with a pointer to the desktop app, and
`tick` does nothing; a row left from an earlier build stays as it is.
Such a server only answers the reads above, the uploads and the
replacement, for a link kept elsewhere.

A **link** is a row of users.db `folder_links` ([user_db.md](user_db.md);
migration steps 35 and 46). Its `kind` is `folder` or `file`. It holds the
workspace, the full `path`, the change-log seq the last round saw
(`cursor`) and that round's `status`. A folder link holds its folder (a
folder block id, or `root`, in `folder_id`), whether notes files are
written, and whether PDFs changed in the directory go back (`push`, off
until asked for). A file link holds its page (`page_id`), `push` always
on, and in `state` what its rounds know of the file: `{doc, size, mtime,
seen?, title}`, the page's title among it. A folder link keeps that in its
directory's state file; a file link has no directory to own. A link whose
folder or page is on another Gamma server also holds that server
(`remote_url`), a token of it (`token`, Fernet-encrypted with the data
directory's key like the mirrors' tokens) and the token's id (`token_id`,
for whoever revokes it there).

The path is the one the user picked, as the machine resolves it. For a
folder (`clean_path`) a relative path, a filesystem root, and anything
inside the data directory are refused. For a file (`clean_file_path`) a
relative path, anything but a `.pdf`, a file that is not there, and
anything inside the data directory are. "Inside the data directory" is
decided by spelling and by identity (`_in_data_dir`: the device and file
id of the path and each directory above it), so another spelling of it
counts too: a `\\?\` device path or an administrative share
(`\\host\C$\…`), which `resolve` keeps as given. The path is unique among the
links ignoring case: one directory per folder link, and one file feeds one
page. A workspace keeps at most ten folders (`MAX_LINKS`) and follows at
most fifty files (`MAX_FILE_LINKS`), local or remote, counted by kind and
by the workspace the folder or page belongs to. A file link's page must be
a page of the library: not a block inside one, not in the trash.

**A link with a remote source** (`create_remote_link`,
`create_remote_file_link`) reads its folder from another Gamma server with
the client's own `RemoteSource`, and sends to it with `RemoteSink`. The
server then runs for that folder what `gamma_sync.py` would run on a PC:

- `workspace_id` is the workspace on the other server, and the state file
  says `server: <url>`.
- The rounds go over HTTP: the three reads above and `/api/uploads/`. Only
  the folder's files come down, never the rest of the workspace.
- The token is checked on creation. `/api/sync/whoami` names its
  workspace, which must match the request's `workspace` when one is given,
  and the folder or page must exist there (a page through `GET
  /api/blocks/{id}`). Read scope is enough for a folder that does not send
  back. A file link, and a folder link with push on, need a token that may
  write. The token is never answered back. The check waits at most
  `ASK_TIMEOUT_S` (15 s) on a silent server.
- Turning push on for a remote folder (`set_push`, `PATCH {push, token,
  token_id}`) checks the write token given on the other server and stores
  it in place of the link's own; without one, the stored token must be a
  write token. Turning push off keeps the token the link has.
  `set_push` and `set_notes` change the row only: the state file is the
  rounds' (one may be running, and saves its files map at its end), and
  each round first copies the row's `notes` and `push` into it.
- The link belongs to the account that made it, not to a workspace. Only
  that account lists, changes and removes it.
- The token expires as its issuer set it (the desktop app mints a year).
  A refused token is the round's error; making the link again with a new
  token adopts the directory.
- Removing the link leaves the token on the other server. Whoever minted
  it revokes it, as the desktop app does.

A folder link's directory is the round's. `_ensure_state` writes the
state file on creation (`server: "local"`, or the remote's address), with
the row's `notes` and `push`, and again at the start of each round. It adopts one that an earlier link to the
same folder of the same server left behind, and refuses a directory
holding another folder's files.

**A file link's round** (`_file_round`) is one `Push` of its file in mode
`file` and nothing else: no state file, no pull. Its status is `{running,
started_at, last_sync, last_error, counts: {sent, waiting, kept}, waiting,
last_sent, title, relocation_error?}`. `waiting` is why a file still being
written, or missing, waits (no failure); `last_sent` the time of the last
send; `title` the page's as the last send left it; `relocation_error` the
last send's. A file the page did not take (kept: the page gone, the bytes
another page's, a refused token, the account no longer an editor) fails
the link with that reason as `last_error`, so the retry wait applies, and
the next round tries again.

**A folder link's round** (`_folder_round`) is a `Round` with the link's
source and, when it pushes, its sink. Its status is `{running, last_sync,
last_error, counts, kept, folder_path}`, each of `kept` (the first fifty)
`{path, why, force}`. `force` is true for a file a forced round
(`?force=1`) replaces with Gamma's: one changed on disk, or changed here
and in Gamma. It is false for a PDF a send could not deliver (Gamma's file
did not change, so a forced round has nothing to replace), a file in the
way that the round did not write, and a file it could not fetch. A round
that fails in a way no rule names (any other exception) still ends:
`last_error` reads `unexpected error: …`, the traceback goes to the log,
and the retry wait applies.

Rounds run three ways: in the background when a link is made, asked to
sync or resumed, inline for a sync asked to wait, and from `tick`. The
app's periodic loop runs `tick` every `TICK_S` (30 s), and a link that is
not paused is *due* when:

- a file link's file moved since its entry (`gamma_sync.moved`, a stat).
- a folder link with push has a PDF that moved in its directory since a
  round recorded it (`_disk_moved`, `gamma_sync.pdfs_moved`): a stat per
  PDF entry, the state file read again only when it changed itself.
- a folder link's source moved past its cursor. For a local workspace that is its
  change log, read directly, so a quiet workspace costs one query per
  link. For a remote source it is the other server's change feed
  (`gamma_sync.changed_since`, `GET /sync/changes?since=<cursor>&limit=1`),
  asked at most every `REMOTE_POLL_S` (60 s) and waited on at most
  `ASK_TIMEOUT_S` (15 s). A server that cannot be
  reached or refuses the token counts as a failed round.
- it has never synced.
- its last round failed `RETRY_S` (5 min) ago or longer.

The tick runs a due link of this server's workspace itself, one after
another. It hands a remote link's round, folder or file, to a thread of
its own
(`run_in_background`), whose `running` mark keeps the next tick off that
link, so a slow or silent server never holds up the rest. A round's own
requests may wait two minutes each (`gamma_sync.Server.timeout`), long
enough for a large PDF.

A paused link (`set_paused`, `PATCH {paused}`) is never due; a sync asked
for still runs it, and a round already running finishes. The pause is
`status.paused_at`, when it began. It is the user's, not a round's:
`_write_status` keeps it as stored whatever the round read when it began,
so a pause set mid-round holds.

A round marked running for over ten minutes counts as dead. One round runs
per link at a time (`_locks`). The tick forgets a link whose workspace on
this server is gone. Removing a folder link leaves the directory as it
is. With `remove_files` it takes back what the rounds wrote and nothing
else (`_remove_written`): the files as recorded and unchanged, the
directories they made when empty, the state file, and the directory when
that leaves it empty. Removing a file link never touches the file.

`/api/folder-links` ([api.md](api.md)) is session-only and never a guest's.
A link of this server's workspace takes the editor role to make, change,
sync or remove; any member lists them. A link with a remote source is its
account's alone. The listing carries each link's `kind`, `page_id`,
`push` and, for a file link, the page's `title`; a file link's `state` and
every token stay on the server. Notes and push are a folder link's: a
`PATCH` of either on a file link is a 400. The desktop app is the only
caller. It makes, pauses, syncs and stops links, and turns a folder's
push on and off, from its bar's sync panel, from a folder's *Keep on this
computer…* and from a page's *Follow a file on this computer…* in Gamma's
own menus ([desktop architecture](../../desktop/docs/architecture.md)
"Folders and files on this computer"). Gamma's Settings has no section for
them.

## The client

`gamma/gamma_sync.py` runs on its own: copy the one file to any computer
with Python 3.10 or newer and a token from Settings → Integrations (read
access is enough, unless PDFs go back: then a token that may write).
`--help` on each command.

| Command | Does |
|---|---|
| `folders --server URL [--token T]` | lists the folders with their ids, and `root` |
| `init DEST --server URL --folder PATH\|ID\|root [--token T] [--save-token] [--no-notes] [--push]` | links the directory (made if missing): checks the token with `/sync/whoami`, resolves the folder by path (slashes between names, case and spaces ignored) or id, writes the state file; `--push` sends the PDFs you change there back to Gamma and needs a write token |
| `sync DEST [--watch SECONDS] [--full] [--force] [--dry-run] [--token T]` | one round, or with `--watch` a round after every change, found by polling the change feed from the manifest's cursor and, for a directory linked with `--push`, by a stat of its PDFs |
| `status DEST` | what the directory is linked to and holds, and whether PDFs go back |
| `push FILE --page ID --server URL [--token T] [--watch SECONDS] [--dry-run]` | makes the PDF the page's file when its bytes are not the page's already: one call for a build script or a CI job, or with `--watch` each new version |

The token comes from `--token`, the `GAMMA_TOKEN` environment variable, or
the state file when `init --save-token` kept it there (then anyone with
the directory has it; a shared directory should not). `--watch` keeps the
process running: after a round it sleeps the interval, asks the change
feed whether anything moved past the cursor, and runs the next round only
then, or at once when a PDF moved in a directory that sends back; errors
are printed and retried at the next interval. Exit code 0
when the round ran (kept files are reported, not errors), 1 on a refused
token, an unreachable server or a missing link. The console is told to
replace characters it cannot show, so a title in another script never
stops a round on Windows.

`push` keeps no state file: the page is the state. It checks that the
token may write and that the page exists, waits until the file was last
written `SETTLE_S` ago (it runs the moment a build ends), and runs one
`Push` of the file in mode `file`: hashed, compared with the page's
`doc_id`, sent when they differ. It prints one line: `sent` (with the
reason when the highlights could not be moved), `up to date`, `waiting:
<why>` for a file not whole yet, or `not sent: <why>`. With `--watch` it
looks at the file every interval and runs again when it moved: a file
that waits is asked about again at the next look, and one that was not
sent (an unreachable server among the reasons) once it changes again. The
exit code is 0 whether or not anything was sent, 1 when the token is
refused or may only read, the page is not there, or the server cannot be
reached at the start.

## Limits

- Notes files go one way: a change to one stays on disk, and the research
  note describes their way back. PDFs go back only from a link that sends
  them: a file link, or a folder link with push on.
- A new PDF dropped in a kept directory stays on disk: only the PDFs a
  round wrote are sent back. The embedded annotations of a PDF sent back
  are not imported; the page's *Import* → *Annotations in this PDF* does
  that.
- A PDF goes up as one request (`POST /api/uploads`), never in parts, so a
  proxy in front of the server refuses a file past its body limit.
- A sent file is told apart by its stat: a build that writes the same
  bytes again sends nothing, and a change made in Gamma by hand stays
  until the file changes.
- A link's text (`[[Title]]`) lags when the *other* page's title changes,
  until the linking page changes or a full round.
- Chats, reading positions and the trash do not travel, as for the mirror.
- Only the desktop app keeps folders and files on disk ("Folders kept by
  the desktop app" above); a computer without the app runs the client.
- A link to a folder or page of a local server's own workspace syncs while
  that server runs: outside background mode, the app starts at launch only
  the servers holding clones or a Zotero link and the folder host, so such
  a link waits for its server to be opened.
- A remote source's token expires as its issuer set; see the same section.
