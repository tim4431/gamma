# Synced vaults

A **synced vault** pairs something in Gamma with something on this
computer and says which way changes go. Its scope is a folder of a
workspace and a directory, or one page and one PDF file; its direction is
two switches, **pull** (Gamma to disk) and **push** (disk to Gamma: off,
PDFs, or everything). A folder vault that pulls and pushes everything is a
folder of Gamma and an Obsidian-style directory kept in step both ways: a
note edited in either place reaches the other, a new file becomes a page,
a page renamed renames its file. A vault that only pulls writes Gamma's
pages as files: what changes on disk is kept and reported, never sent
back.

Only the desktop app's own server keeps vaults (`GAMMA_FOLDER_LINKS`,
`config.folder_links_enabled`). On any other server, a NAS among them,
`/api/folder-links*` answers 404 with a pointer to the desktop app and the
tick does nothing; such a server only answers the routes a vault kept
elsewhere reads and writes through. A computer without the app runs the
gamma-sync client ("The client"), which pulls a folder and pushes PDFs.

Besides vaults, the same server reads a PDF where it lies on this computer
(*Open with*, *Open a PDF…*), the page reading the file in place instead of
a stored copy ("A PDF read where it is"). A vault's PDFs are read the same
way.

The design and its reasons are in the research notes
[vaults.md](../research/vaults.md) (the synced vault, the lens, the
rules), [folder-sync.md](../research/folder-sync.md) (Gamma to disk) and
[disk-to-gamma.md](../research/disk-to-gamma.md) (PDFs back to Gamma).

Code:

- `gamma/folder_links.py`: the vaults' rows, the refusals (`check_vault`),
  making, changing, relinking and removing a vault, a page's standing and
  its notes file for Gamma's file editor, the file vault's round and its
  in-process sink (`LocalSink`), the tick and the hot loop.
- `gamma/routers/folder_links.py`: `/api/folder-links*`.
- `gamma/vault_sync.py`: a folder vault's rounds (`Round`, `sync`,
  `sync_page`) and the remote of this server's workspace (`LocalRemote`).
- `gamma/vault_disk.py`: the directory as files: the marker, the scan, the
  evidence of a move, the write that replaces a note, the vault's trash.
- `gamma/vault_store.py`: vaults.db's rows of a vault: the bases, the
  journal, tombstones, directories, history.
- `gamma/vault_merge.py`: the three-way merge of a note changed on disk.
- `gamma/md_lens.py`: the lens between a Markdown file and a page's blocks.
- `gamma/vault_links.py`: links, pictures and highlights, block by block,
  between the file's text and Gamma's.
- `gamma/vault_upgrade.py`: a folder kept on disk before synced vaults,
  taken onto the engine.
- `gamma/vault_http.py`: the remote of a folder on another Gamma server
  (`HttpRemote`).
- `gamma/linked_files.py`: PDFs read where they are, *Opened files*.
- `gamma/storage.py` `open_path`, `readable`, `is_linked`, `linked_stat`,
  `linked_moved`: a stored name served from a file on this computer.
- `gamma/markdown_import.py` `md_to_blocks(spans=True)`: the one parser, in
  the mode the lens reads with.
- `gamma/gamma_sync.py`: the gamma-sync client, which is also the HTTP
  client (`Server`), the PDF push (`Push`) and the settle test (`settled`)
  the server's vaults use; the standard library only.
- `gamma/folder_sync.py`, `gamma/routers/sync.py`: the reads the client
  pulls through (`GET /api/sync/folders*`).
- `gamma/ops.py` `commit_plan`, `gamma/routers/collab.py` `POST
  /api/pages/{id}/plan`: a plan from a vault on another computer.
- `gamma/sync_engine.py` `VAULT_KINDS`: the vaults' rows of
  `sync_conflicts`, kept apart from a mirror's and a Zotero link's.
- `gamma/routers/pages.py` `replace_attachment`: the replacement a PDF push
  makes, shared with `PUT /api/pages/{id}/attachment`.
- `gamma/routers/uploads.py`: serving a linked file.
- `frontend/src/vault/`: the page's standing (`useVault.js`,
  `vaultState.js`), its chip and banner (`VaultChip.jsx`), the notes lock
  (`notesLock.js`), Gamma's file editor (`FileSourceDialog.jsx`), *Add to
  library…* (`FolderPickerDialog.jsx`); the merge chip's side in
  `frontend/src/collaboration/conflictKinds.js` and `MergeResolver.jsx`.
- `desktop/main.js`, `desktop/lib/keeping.js`, `desktop/ui/bar.html`,
  `desktop/preload.js`, `desktop/lib/sidecar.js`: the shell's part ("The
  desktop app").

Tests:

- `backend/tests/test_md_lens.py` (the lens) and
  `backend/tests/test_md_lens_corpus.py` (the gate, "The lens"), with the
  Obsidian-style corpus in `backend/tests/vault_fixtures/`.
- `backend/tests/test_vault_disk.py`, `test_vault_store.py`,
  `test_vault_merge.py`, `test_vault_links.py`: the pure and the disk
  layers.
- `backend/tests/test_vault_sync.py` (rounds), `test_vault_routes.py` (the
  routes), `test_vault_upgrade.py` (kept folders), `test_vault_http.py` (a
  vault of another server).
- `backend/tests/test_linked_files.py` (PDFs read where they are),
  `test_folder_links.py` (vaults' rows, refusals, the tick, Sync now
  answered with its round running, `running` read from the round's lock,
  a round's progress),
  `test_disk_push.py` (PDF pushes, file vaults), `test_folder_sync.py` (the
  client's reads and the client), `test_upload_retention.py` (the files a
  replacement leaves).
- `frontend/tests/vaultState.test.mjs`, `notesLock.test.mjs`,
  `openedFiles.test.mjs`, `conflictKinds.test.mjs`; the browser group
  `vaults` (`frontend/tests/e2e/scenarios/vaults.mjs`).
- `desktop/test/main.test.js` (a vault synced from the panel followed to
  its round's end, among the rest), `keeping.test.js`, `openFiles.test.js`,
  and the desktop suite's vault and opened-PDF steps (`desktop/test/e2e.js`).

## The words

The interface uses a few words everywhere, git's and the clone settings'.
Gamma's server is the **remote**, the directory or the file here is
**local**:

| Word | Means |
|---|---|
| **synced vault** (同步仓库), vault | a link between Gamma and this computer: a scope and a direction |
| **pull** (拉取) | Gamma to disk: pages become files, renames move them, deletions trash them |
| **push** (推送) | disk to Gamma: files become pages, a changed note goes into its page, a PDF becomes its page's file |
| **sync** | push, then pull, each where its switch is on: the disk's changes are read first |
| **up to date** | nothing to pull or push |
| **conflict** (冲突) | a block changed on both sides since the last sync: the file's text won, Gamma's is kept |
| **untracked** | a file in the directory a vault does not read: one its `.gammaignore` leaves out (with none, any kind but notes, PDFs and pictures), or one it takes of a kind Gamma does not read yet |
| **work folder** (工作文件夹) | a vault of a folder of this server that pushes everything: the files its directory holds and it tracks are pages that follow the disk ([work folders](work_folders.md)) |
| **Choose files…** (选择文件…) | what a work folder tracks, chosen in a dialog and kept as its `.gammaignore` |
| **force pull**, **force push** | one side made the other's, after a count and a confirmation |
| **remove the vault** | the link goes; its pages stay; its files stay, or the ones pull made go to its trash |

*Clone* (a workspace of another server), *commit*, *branch*, *fetch* and
*stash* are never shown. Where the words show:

- A library folder's menu: *Sync with this computer…*; a work folder's:
  *Choose files…*. A page's Document menu: *Push from a file on this
  computer…*. The launcher and the sync panel: *Add a synced vault…*,
  which starts from a directory. The desktop app's Server menu: *Open a
  PDF…*.
- The sync panel: a row per vault with its name, its direction and its
  state, its place and its remote in the row's tooltip. Its *…* opens the
  vault's view: the switches *Pull* and *Push* (a work folder's *Add its
  PDFs and notes to the library* too), then *Sync now*, *Pull now*, *Push
  now*, *Pause* and the directory as a bar of buttons named by their
  tooltips, and *Choose files…* for a work folder, *Go on…* for a held
  vault, *Locate…* for a stopped one, *Force pull…*, *Force push…* and
  *Remove the vault…* as rows.
- A page of a vault: a chip among the page header's buttons, one icon for
  how the page stands: kept in step, pushed from a file of its own,
  paused, or something to see to. Its tooltip says it in words, *Synced:
  Attention.md* (*Pushed from: main.pdf* for a vault of one file). Its
  card holds the path, the direction (*Pull: on · Push: everything*),
  *Show in folder* and *Edit the file*; a work folder's file's adds *Add
  to library…* or *Remove from library*, *Save a copy to library…* for a
  PDF, and *Choose files…*. A banner, *Not written to disk:
  …*, shows when the last write was refused or the vault reaches no file
  now (stopped, offline, held, failed). An opened PDF: a disk, *On this
  computer*, or the alert icon and *File missing*.
- The client: `pull`, `sync`, `push`.

In the code the table stays `folder_links` and the API
`/api/folder-links`. A row's `kind` is the scope, `folder` or `file`;
`pull` is 0 or 1; `push` is 0, 1 or 2 (`PUSH_OFF`, `PUSH_PDFS`,
`PUSH_ALL`), which the API says as `off`, `pdfs`, `everything`
(`push_word`, `push_value`). A vault's writes in Gamma are logged as the
client `disk` (`auth.DISK_CLIENT`), as the account that made the vault:
they make no Inbox item and wake no round of their own.

| Scope | In Gamma | On this computer | Pull | Push |
|---|---|---|---|---|
| `folder` | a folder, or `root` (the whole library) | a directory | on or off | off, PDFs or everything (not the whole library, but one that pushed everything before work folders) |
| `file` | one page of the library | one `.pdf` | never | its PDF, always |

A vault pulls, pushes, or both: one switch stays on. A folder vault that
pushes everything writes notes files whatever was asked. A vault of one
file never writes: its file becomes the page's PDF each time it changes, a
manuscript built again from its TeX say. It is made by hand for a file in
a directory that is not wanted as a vault, and for a page on a server that
cannot read this disk; a directory vault reads its PDFs the same way by
rule.

## What lands on disk

```text
<vault>/
  .gamma/
    vault.json              the marker: format, id, notes_folder
    trash/<stamp>/<path>    the files of pages that left the vault
    feedback/               a work folder's feedback files: open.json, open.md, img/, requests/ (feedback.md "The files")
  .gammaignore              what the vault tracks, where one says (Choose files… writes it)
  Sub/                      a folder below the vault's folder
    A paper.pdf             a paper, read where it is
    A paper.md              its notes and highlights
  A note.md                 a page without a PDF
  attachments/<hash>.png    a picture pasted in Gamma
```

**The marker**, `.gamma/vault.json`, is `{"format": "gamma-vault-1", "id":
<random hex>, "notes_folder": ""}`. It is written once, when
the vault is made (`vault_disk.make_marker`), in a hidden directory (the
hidden attribute on Windows, the dot elsewhere); no round changes it. Its
id is the row's `state.vault`, and each round checks it before and after
its scan: it is what tells an unplugged disk or an empty mount point from
an emptied vault, and a copy of the directory from the vault. A round never
makes the vault's root, since `mkdir` on an empty mount point would hide a
disk that is not there; only making the vault does. `notes_folder` names a
directory where new notes of papers go; no route sets it, so it is what a
person wrote in the file. An older marker's `ignore` globs are read once,
written as the vault's `.gammaignore` ("A work folder's round"), and never
again. A vault of one file writes nothing on disk, ever.

**The files.** Each folder below the vault's folder is a directory, named
after the folder (`obsidian_export.vault_name`; a name the scan would leave
out, `~$Plan`, takes a `_` before it). A page appears once, under its first
filing below the vault's folder, the filing of its top page for a page
inside a page. A page filed below two vaults of this server is one
vault's (`vault_sync.remote_for`): a vault leaves out the pages of another
that may take them, one that pushes or, when this one only pulls too, an
older one that only pulls. Such a vault inside its folder takes them
whatever its age; one beside it (a page filed in both) takes them when it
is the older, and this one reports them (`status.shared`). A vault that
only pulls never takes a page from one that pushes: both write it, the one
a copy in its directory and nothing into Gamma. A page's notes are `<dir>/<title>.md`; a paper's
notes go beside its PDF under the PDF's stem, or into `notes_folder`. The
PDF pull copies out of Gamma goes beside the notes under their stem, else
into the folder's directory under the title. Every name is made free in its
directory ignoring case (`vault_disk.free_name`, the one naming rule:
` 2`, ` 3`…), against what the disk holds and what the rows hold, since a
file missing for one scan still holds its path. The notes file is the
page's blocks with text and its highlights, in the dialect "The lens" and
"Links and highlights" describe; ink, text boxes, pictures on PDF pages,
sheets, recordings and a PDF's link regions stay in Gamma. Its front
matter carries `tags` (the page's labels) and `aliases`, on a note pull
made; a person's note gets no front matter it did not have, and a key it
holds is patched only when Gamma's value differs from what push reads from
it. No `gamma_id` line is written: vaults.db says which page a file is.

**vaults.db**, in the data directory beside users.db
(`config.VAULTS_DB`, `db.connect_vaults_db`), is what this computer knows
of its vaults and of the PDFs it reads in place. It exists only where
vaults are kept, and its statements (`db.VAULTS_SCHEMA`) are additive and
applied when a connection opens, never migrated. It is never in a
workspace's files: a workspace backup that carried a path could make a
restored server read an arbitrary file of the computer it lands on. Its
tables ([user_db.md](user_db.md) has the columns):

- `linked_files`: a stored name (`<hash>.pdf`, `documents.model.stored_name`,
  or a picture's upload name) and the path that holds it, the size, time
  and file id when last read, the page (`''` for a vault's picture), the account that opened the
  file, whom a change of it is made as (`actor`), and why the last replacement placed no
  highlights (`relocation_error`).
- `vault_files`: a vault's files as its last round left them, one row a
  path, each a **base** ("A round").
- `vault_dirs`: a vault's directories, each with its folder and its file id.
- `vault_history`: bytes a write replaced, zlib-compressed.

Each `vault_store` function opens its own short connection and commits; none
runs while a workspace's pages.db write lock is held, so a slow disk under
vaults.db never holds every writer of a workspace.

## A PDF read where it is

A page whose PDF stays at a path on this computer has its document read in
place (`doc.at` is `path`, `documents.model.in_place`; the document's shape
is in `gamma/documents/model.py`, [documents.md](documents.md)) and a
stored name like any other,
`<hash>.pdf` (`stored_name`), its hash the SHA-256 prefix the bytes would
get if stored; a `linked_files` row says which path holds that name.
Nothing is copied, and nothing is ever written into the file or beside it.

**The read.** `storage.open_path` answers a name from the uploads directory,
else from a linked file (`linked_path`, only where vaults are kept): the
first of the name's paths whose size and time are as recorded, so what is
read is what was hashed. A path whose stat moved reads as nothing, and the
listeners of `storage.linked_moved` hear of it; one that is gone reads as
nothing and is the page's missing file. The read never hashes and never
waits: it runs on every request, HEAD and range request. `linked_stat`
looks at a path at most once a second (`LINKED_STAT_S`, at most
`LINKED_STATS_MAX` paths remembered); a failed look asks the path's disk
(its drive or share, else its first directories), and a disk that does not
answer either is offline for `OFFLINE_S` (30 s), every path on it reading
as nothing with no look, since a dead share holds a look, and a pool
thread with it, for many seconds.

**The worker.** `linked_files` takes the moved names off a queue in one
thread (`follow`). A file of the recorded size is hashed at once: the same
bytes, only touched, refresh the row's stat. Other bytes wait until they
have settled (`gamma_sync.settled`: written at least `SETTLE_S`, 3 s, ago,
open for reading, `%%EOF` in the last kilobyte), so that a LaTeX build's
half-written PDF is never a page's; a file still being written is looked
at again after `LOOK_AGAIN_S` (1 s). Settled new bytes become the page's
PDF (`_take_bytes`): the row under the new name first, so the relocation
run reads the new file, then the replacement a PDF push makes
(`LocalSink.replace` with `check_held=False`: bytes another page holds are
no refusal, the disk is what it is), as the row's `actor`, or the
workspace's first owner when that account may no longer edit there
(`_sink`); the new row names the opener still, so keeping the file pushing
and taking it elsewhere stay that account's. The
highlights are placed again, the open viewers switch on the ops, and a
replacement whose relocation run could not start keeps the reason on the
row for the page's card. A page in Recently deleted waits; a row whose page
now carries another PDF goes. A vault's PDF (its page has a `vault_files`
row of kind `pdf`) is its round's alone: one replacer per file
(`_in_vault`).

**The upload route** (`GET /api/uploads/<name>`): a name with no file,
whose linked file the worker has in hand, waits for the worker up to
`SETTLE_WAIT_S` (3 s) and answers from what it found (`settle_wait`): the
same bytes are served, new bytes leave the old name with nothing and the
ops that switch the viewer are on their way, so a file only touched is no
failure. A linked file is served `Cache-Control: no-cache`, with
FileResponse's ETag of its size and time: unlike a stored name, which is
`immutable`, its path can hold other bytes tomorrow.

**A version kept while its comments are open.** A page read in place
that has an open comment thread keeps the bytes it shows stored
(`uploads/<hash>.pdf`, the name a `linked_files` row holds too), and the
version each thread was opened on is held until the thread is resolved:
the relocation run and the feedback reader go on reading it after the
next build at the path takes the file's bytes away. The thread's open
stores the current bytes, a replacement while a thread is open stores the
incoming ones first (`feedback.kept.keeps_bytes`, the one predicate with a
library file's versions, "A work folder's round"), and the sweep below
stores what was left unstored ([feedback.md](feedback.md) "The commented
version kept").

**The sweep** (`linked_files.sweep`, from each tick): the opened pages to
forget (below), the bytes of a page with an open thread not stored yet
(`feedback.kept.repair`), the rows of workspaces that are gone, then every
row's stat, a moved one queued as a read would queue it. A read may never
come for a file an open viewer holds, and a file that was still being
written has nobody else to look at it again.

**What never sees a linked file.** `storage.exists`, `size`, `stat`,
`touch` and `list` mean "stored here", which keeps linked files out of the
quota, the orphan purge, backups, the off-site copy and a mirror;
`readable` asks "can this be read at all". A lookup by attachment
(`blocks_store.page_for_doc`, the clipper's match, a backup's merge) leaves
linked pages out, and a work folder's files not in the library
(`work_folders.library.SQL_LIBRARY_PDF`): their bytes stand for no page of
the library. A Gamma export carries the document as stored
(`at` `store`), so the page lands elsewhere with its bytes and no path.
Importing a PDF's embedded annotations never strips a linked file. The
search indexer leaves out a linked file it
cannot read now, so the pending count comes down. An error a job or a
round reports says what went wrong without the path an `OSError` or MuPDF
names (`jobs.error_text`): the path is the person's own.

**Opened files.** *Open with* and *Open a PDF…* go to `POST
/api/folder-links/open` (`linked_files.open_pdf`). The path is checked as
a file vault's is (`folder_links.clean_file_path`), then, in this order: a
file inside a directory vault opens that vault's page (a vault that pushes
everything runs a round first for a file it has not read yet), a file that
is a vault of one file opens that page (either may be in another
workspace), bytes a page of the workspace stores open that page, and the
page that holds the path opens, whatever became of it: back from Recently
deleted, its PDF replaced when the bytes changed, moved to the top of
*Opened files*. Otherwise a new page is made inside *Opened files*, a page
found by its property (`opened_files: true`) and made on demand, newest
first: a sub-page is the one way a page stays out of the library's list
that server and client share. The new page is titled by the file's stem,
with no `auto_title` (a lookup fills fields and never renames), and its
metadata lookup is scheduled. A clone's workspace takes no new page. One
open or locate runs at a time, so a path has one page.

- **Add to library…** (`POST /opened/{id}/add`, `add_to_library`): the
  bytes are stored (the same hash, so the page's document keeps its hash
  and name), the page leaves *Opened files* for a folder or the library's
  top, and it no longer reads the file (its document at `store`,
  `document_at`). `keep_pushing` makes the file a vault of one file,
  made before the page moves, so that a refusal leaves the page as it was.
  With `workspace`, another workspace of this server the account edits,
  the page goes there with everything on it (`_carry`): exported as a Gamma
  export is and merged in as an import is
  ([import_export.md](import_export.md#gamma-to-gamma-export)). Then the
  page and the pages inside it go to Recently deleted here
  (`ops.trash_page`), everything on them kept 30 days for a restore, and
  their `linked_files` rows go once they are there (vaults.db is not written
  inside the write lock): the file opened again is a new page, not the
  trashed one back (`_rows_standing` finds no row), and the page restored
  is back whole inside *Opened files*, reading no file. Nothing on the
  carry deletes a page here for good. A clone refuses it, and so does a
  workspace that holds the page, a page inside it
  (`blocks_store.with_sub_pages`) or a page of their bytes already, even
  in its Recently deleted, checked before the export (`HELD_THERE`). What
  goes wrong after the merge takes back out the pages that merge added, by
  its own list (`added`), each alone (`ops.purge_pages`: a page inside one
  of them that came there another way stays, a library page there now),
  and moves nothing here: the merge still skipping a page (`pages_skipped`,
  one that came to be there meanwhile), a refused `keep_pushing`, or the
  pages here written on since the export. A digest of what the export
  reads (`_carried`: the pages, their blocks, comments and chats) is taken
  before it and again under the write lock that moves them here, so a
  note, a page inside it, a comment or a chat written meanwhile is never
  lost: the page stays here as it is and the person tries again
  (`CHANGED_HERE`). The take-back deletes for good, but only a copy as the
  merge left it (`_carried` just after the merge, again under the other
  workspace's write lock), whose every note is still here; a copy someone
  wrote on there meanwhile stays there whole, and the person is told the
  page is in both workspaces now (`IN_BOTH`). Trashing the copy instead
  would leave in that workspace's Recently deleted a page its members never
  saw arrive and refuse the next carry (`HELD_THERE` counts it); were that
  check to let it by, that carry's merge would delete it for good, since a
  merge replaces a page that is only in Recently deleted.
  `keep_pushing` and `workspace` are the opener's alone (`_openers`: the
  account its rows name), as a vault's disk is its maker's: the file is on
  that account's computer, and another editor stores its bytes here at most
  (400).
- **To another server** (the desktop app's `carry-to-server`, only for the
  opener, `reader.mine`): the same rule, with the export and the merge made
  by the shell over HTTP. It reads the digest first (`GET
  /opened/{id}/carried`, `carry_digest`, `{digest, pages}`: the ids of the
  page and the pages inside it, the page first; 400 when a page inside reads
  a file that is gone or changed, `_inside_at_hand`, as for another
  workspace). It looks for each of those pages there, live or in Recently
  deleted, and stops on any (the merge would replace a copy in Recently
  deleted for good), exports the page, merges it in there and takes it out
  of *Opened files* there, then hands the digest back (`POST
  /opened/{id}/carried`, `let_go`). Under `_opening` and the write lock, the
  page and the pages inside it go to Recently deleted here without their
  rows when the digest is still theirs (`_let_go`, the tail `_carry` ends
  with too). A page written on since gets 409 (`CHANGED_SINCE`) and nothing
  here is touched; the copy there stays, so the page is in both places, and
  the shell says so. Both routes are the opener's alone (400 for anyone
  else).
- **Locate…** (`POST /opened/{id}/locate`, `locate`): the file found at a
  new path. The same bytes are the page's as they stand, others replace its
  PDF; a path another opened page reads is refused, even from Recently
  deleted. Opening the file again or locating it as another account keeps
  the rows naming the opener (`_opener`); only a page a crash left with no
  row takes the caller.
- **Missing** (`missing`): no copy of the bytes is stored here and none of
  the page's paths holds a file. `reader(ws, page, actor)` gives a page's
  `{path, missing, mine, relocation_error?}` for its chip, `mine` whether
  the asking account opened the file.
- **Forgetting**: a page inside *Opened files* with no mark, comment or
  chat, untouched for `FORGET_DAYS` (90) and open nowhere, goes for good at
  a sweep, looked at again under the write lock; nothing of it is lost, the
  file is where it was. A page in Recently deleted keeps its row, for a
  restore.

In the page, *Move to library* and *Move into page…* on an opened PDF go
to *Add to library…*, since its bytes must be stored before it leaves
(`vaultState.isOpenedPdf`), and *Move to folder ▸* is hidden while an
opened PDF is among the pages acted on. The account preference `openAddsToLibrary`
(Reading, off by default), and the Mac App Store build, make *Open with*
add the file to the library instead.

## The lens

`md_lens` is how a page is written back to its file without a render ever
touching the file's bytes, and how a file is read into blocks. It is pure:
no database, no disk, nothing of Gamma but `markdown_import`.

**One parser.** `parse(raw)` reads a file with the importer's own parser in
spans mode, `md_to_blocks(text, spans=True)`, which records for each block
its source lines, the source line of each content line, the raw
indentation and marker, and the block's class (`item`, `wrapped`, `plain`,
`heading`, `fence`, `math`, `verbatim`, `quote`, `table`). In spans mode
only a space or a tab is whitespace after a list marker, a heading's `#`
marks or a task's box, as in CommonMark and Gamma's editor (`mdLines.js`
`MARKER_RE`): `-\xa0x` and `#\xa0h` are paragraphs, `- [ ]\xa0x` holds no
box, so a block the walk writes anew keeps its no-break space. Nor does
spans mode rewrite a Logseq image size, `![alt](url){:width N}`, into
`![alt|N](url)` as the importer does (`note_markup.obsidian_image_sizes`):
the block's text stays the file's lines, so an edit beside the image never
writes `|N` into the file, and every reader takes both forms
(`note_markup.alt_and_width`, the frontend's `mdMarks.js`). With spans
off it is the importer's parser byte for byte, which
`backend/tests/vault_fixtures/spans_off_digests.json` pins. A `Doc` holds
the byte-order mark, the usual line ending, the lines each with its
terminator (split on `\r\n`, `\r` and `\n`, never `splitlines`, which also
breaks on what the parser takes for text), the front matter, the blocks,
and the **tiles** that lay out every line of the body: a block's own lines,
a gap of blank lines, or an `^anchor` on its own line, which belongs to its
block and follows its subtree. Front matter is only front matter when every
line up to the closing fence is what front matter holds (a key, a list
item, a comment, an indented or blank line); otherwise `---` is a block. A
file past the importer's limits (`MAX_MARKDOWN_BYTES`, too many blocks) or
nested deeper than `MAX_DEPTH` (200) has a `problem` and no blocks, and
can be neither edited nor written. `parse` never raises on text.

An anchor at the end of a line (`text ^a1b2`) counts on a plain block, an
item or a heading, on its last line, and needs a letter (`x ^2` is text);
fences, `$$`, tables and quotes take only the own-line form. The first of
two equal anchors wins, compared ignoring case.

**The walk** (`write(doc, ids, tree)`) writes a tree of blocks,
`{id, content, anchor?, children}` in the file's space, over the file's
lines, depth first:

- a block whose id, text and place are as read is a straight copy of its
  lines: the first law lives here;
- a block whose text changed, in its place and of its class, keeps its
  dress line by line: its old and new lines aligned (`difflib`), a line
  unchanged keeps its raw line, a changed one its leading whitespace when
  that still fits, its trailing whitespace and its line ending, an added
  one the block's continuation prefix, the marker kept while its kind is;
- a block of a new class, or in a new place, is written as a new block
  there, dressed like a sibling near it (`NEARBY`) or like the file (its
  bullet character and indent unit);
- a block whose class cannot hold children (a paragraph, a quote, a table)
  becomes a list item when it has some, so a child of a block that is no
  heading is always a list item in the file.

Between two blocks the walk keeps the blank lines they had, or writes what
Markdown needs (`required`, by CommonMark's rules: one blank line unless
both are items of one list or the first is a heading, a paragraph after a
list or a quote needs one). The byte-order mark, the line ending and the
last newline are the file's.

**Shape** (`shape(doc, ids, tree)`) is what pull calls, never `write`: the
tree a written file reads back as, relative to the file. It writes, reads
the text again, and carries each id by the line its block starts on; a
block a split made has id None. A block whose old dress reads as the block
before it is written as new on the next pass, at most `MAX_PASSES` (3).
Shape may split a block and reparent one, since a file holds only what
Markdown can say, but it **never removes an id**: where a block would be
swallowed it raises `Unwritable` and nothing is written. Its reasons are
`OPEN_BLOCK` (an open fence or `$$`, a block HTML left open would take in,
an anchor code took in), `TOO_DEEP`, "the file would not settle", and the
meaning check's two below. A difference `tree_equal` does not see (outer
whitespace of a line, an escaped caret) stays in the file and is never sent
to Gamma as an op.

Besides: `render(tree)` writes a fresh file (a new note, from no base);
`match(old, new, gone)` pairs a changed file's blocks with its base's, by
anchor first (in the base, then in the blocks the file dropped), then by
identical text in order, then by text among the dropped blocks (its
`content_hash`), then the leftovers between matches pairwise, of one class
and at least `MIN_SIMILAR` (0.3) alike, within `PAIR_WINDOW` (20); the rest
are inserts and deletes. `project(snapshot, kind_of)` is what of a page
reaches its file: highlights and notes with text, a block with no span
removed and its children lifted into its place, so that comparing, shaping
and matching all see a note under a rectangle where the file has it.
`patch_front(doc, fields)` sets front-matter keys: every other key stays
byte for byte, a known key is rewritten in the form it has (a flow or a
block list), a new key goes last, a new front matter first, and an emptied
one goes, never leaving `---` `---`.

**The laws**, tested on every parse in the suite and over the corpus:

1. `write(doc, ids, tree_of(doc, ids))` is the file, for any text.
2. One block's text edited, through `shape`: only that block's lines differ.
3. `shape` converges, and `parse(text)` gives `shaped`.
4. `shape(doc, ids, tree_of(doc, ids))` is that tree and the same text.
5. Blocks an edit did not touch keep their text, parent and bytes.
6. A text edit then its revert gives the original bytes.
7. `shape` never removes an id.

**The meaning check.** The parser is not CommonMark. It reads two blocks
where CommonMark reads one (a lazy line after an item, `Title` over `---`,
a comment over a blank line), reads some code by indentation as text, and
does not see an HTML block left open. The walk follows CommonMark's model
of what stays open after a block (`_end_of`), and only the separators, the
anchors and the columns ask it; a model can miss a case. So before `shape`
hands over a text that differs from the file, a CommonMark parser reads
both the text and the file, and a block the change left alone that would
read otherwise in another app makes the write `Unwritable(MEANING)`: "edit
the file to make it", the person making that change on disk. `MATH_TAIL`
is the same where a formula Gamma changed has text after its closing `$$`,
which other apps read as more of the formula. The parser is markdown-it-py
(`markdown-it-py==4.2.0` in `backend/requirements.txt` and
`requirements-mobile.txt`): CommonMark with tables, the parser family of VS
Code's preview, in pure Python, with one rule of its own for `$$` display
math as remark-math, Obsidian and VS Code read it, since plain CommonMark
reads `text` then `$$` as one paragraph and would refuse ordinary edits
beside a formula. It reads blocks only, as deep as a file here may nest,
and is imported on the first write that asks: a new file, an unchanged
write and a server that never writes a vault never load it.

**The documented limits** (the module's docstring). Where Markdown leaves a
law no way to hold, the file's meaning goes first, and the gate counts each
case by name: a heading moved, added or retyped parents what follows it;
text a list now stands before cannot stay indented past it, so its indent
changes, and code by indentation that has to move becomes a fence (so does
such code the parser reads as text, under an item or a heading); a `---`
the file comes to open with becomes `***`, since it would open front
matter, and a U+FEFF the file would open with gets a byte-order mark before
it; a bullet whose text is a block (`- # H`) stands behind another; no
paragraph can be the first child of an item with nothing on its line; an
empty box with no space after it (`- [ ]`) ends an edit and its revert as
one with a space; a blank line an edit needed outside its block stays on
the revert; text the file would read as an anchor (`press ^c`) has its
caret escaped. Files of blocks at random indents can do worse: a
neighbour's indent or an id's parent can shift. Nothing is lost in any of
them: `shape` says what the file reads.

**The gate**, `backend/tests/test_md_lens_corpus.py`, runs the laws over
every `docs/**/*.md` (this file among them), `gamma/onboarding/welcome.md`,
`backend/tests/notion_fixtures` and the Obsidian-style corpus in
`backend/tests/vault_fixtures/` (front matter with unknown keys, callouts
with folds, anchors, wikilinks, embeds, tasks, tables, setext headings,
HTML blocks, footnotes, `%%comments%%`, `*` bullets, tab indentation),
each fixture also with CRLF, CR, a byte-order mark and no last newline,
made from its LF text as bytes. Each file gets random edits, inserts,
deletes, moves and retypes through `shape`, from a seeded `random.Random`
so a failure names what replays it, and law 1 runs on random bytes. The
refusals it allows are `OPEN_BLOCK` (a file with an unclosed fence or `$$`,
a change that would put a block in open HTML) and `MEANING` or
`MATH_TAIL`, counted by the kind of change; any other failure is a bug. It
runs in under a minute:

```text
cd backend
venv\Scripts\python.exe -m pytest tests/test_md_lens_corpus.py -q -s
```

`-s` prints the counts (`gate: {...}`): the documented limits met, the
refusals, the reverts checked. A doc edit can make the gate meet a new
case, so a change under `docs/` runs it too.

## Links and highlights

`vault_links` turns one block's text between the file's space, where the
lens works, and Gamma's. Gamma holds a link to a page or a block as
`[[id]]` (`![[id]]` embeds a block) and a picture as its upload's address;
the file says `[[Title]]`, `[[dir/Title|alias]]`, `[text](<a b.md>)`,
`![[fig.png|300]]`. `to_gamma(file_text, memo, ctx, base)` reads a block's
file text with a memo of how each link was written, keyed by what it
resolved to (a page or block id, or a vault path: an upload's address holds
its hash, which changes when the picture does); `written` writes Gamma's
text back, each link as the memo remembers it where it can and in the
vault's habit otherwise.

**Identity first.** The engine calls them only for a block whose text
changed on that side: a block whose Gamma text is its base's keeps its file
text, and a block whose file text is its base's keeps its Gamma text. So a
link no reading gives back as written (`%20`, a title in a link, an alias,
`![a|300]`) never makes an untouched block read as changed.

**Resolution** is a Markdown zip's (`markdown_zip_links.resolve_path`):
relative to the note, then by vault path, then by name anywhere, ignoring
case, names NFC. A link that resolves to nothing stays as typed, unless
the memo remembers its spelling: `[[Old]]` whose note was renamed stays a
mention of that page. Two links to one target are told apart by aligning
the base's text with the new one. A page outside the vault is its absolute
Gamma address (`<server>/?ws=<id>`, from `cloud_auth.server_url`), read
back as a mention; a server that knows no address of its own leaves such a
link as Gamma wrote it. Nothing inside code or math is a link, here as on
Gamma's screen. A new link takes the vault's habit from
`.obsidian/app.json` (`useMarkdownLinks`, `newLinkFormat`: shortest,
relative or absolute, `attachmentFolderPath`), else wikilinks by the
shortest path.

**Anchors.** A link to a block of another note asks an anchor in that
note's file (`LinkContext.new_anchors`): the ask is kept on that note's row
(its node's `a`, its seq none) before this note is written, and the other
note is written this round when it can be, as a change Gamma made. A note
whose base names none of its blocks (a kept folder's, "Kept folders and the
upgrade") keeps that base, never one of no blocks, which the next push
would read as every block new: it takes the ask only when this round
writes it anew. An anchor is never removed because a link went. A block of a note the round
cannot write (its vault does not pull, its file has a problem or is a
cloud placeholder) takes no anchor, and a link to it is a link to its page.

**Pictures.** A picture pasted in Gamma goes to the vault's attachment
folder under its upload's name (`LinkContext.new_files`): `attachments/`
when the vault has no `.obsidian/app.json`, the vault's root when it has
one without the key, as Obsidian does. Pull writes it once and it is read
in place from then on. An upload of a kind Gamma never stores or serves
(`documents.formats.media_type` refuses it: an executable a .gamma import or
another server holds) goes into no vault: its link stays as Gamma wrote
it (`LinkContext._file_path`). A picture of the vault a note links is read where it
is, under its content digest's name (`LocalRemote.link_file`: a
`linked_files` row with no page), so the page shows it with nothing stored.

**A highlight** is the one block of its own dialect, which differs from
the vault export's on purpose:

```text
> [!quote] [[Paper.pdf#page=3|p. 3]]
> the quoted text
>
> the person's note, every line

^a1b2c3
```

To the lens it is a quote block with an own-line anchor, Obsidian's rule
for a quote; a new highlight's anchor is its block id
(`obsidian_export.anchor_marker`, a `b` before one with no letter). In
Gamma it is `highlight(note, quote, page)`: the block's content, its quote
and its PDF page. The one test is the first line: `> [!quote`, in any case,
folded or not. A block matched to a highlight, by anchor or by text, stays
a highlight whatever its title or quote say now: its quote and page are
Gamma's, and only its note is read, by taking the known quote's lines out
(a line at least `QUOTE_ALIKE`, 0.5, alike word for word is the quote as
the person edited it; lazy lines count as note). A person's edit of the
quote stays in the file until Gamma's quote or page changes. A new
`[!quote]` callout whose title links the page's own PDF, with no anchor
Gamma knows (a vault copied from another computer), becomes a highlight of
that quote and page; the relocation run then places it where its quote is
in the PDF, dashed, for the person to check, and a run that cannot start
now waits in the vault's state (`placing`) for the next whole round
([highlight_relocation.md](highlight_relocation.md)). A highlight the file
drops goes with its properties into the base's `gone` for 30 days, so its
rectangles come back if the text does.

## A round

`vault_sync.sync(link)` is a folder vault's whole round, `Round.run`: open
the vault, push, then pull, each where its switch is on, and ask again for
any relocation run that waits. A round reads and writes Gamma through a
**remote**, the small interface of what it needs there: the pages below
the folder with their change-log seqs (`layout`), a page's blocks (`page`),
ops applied under the workspace's write lock (`apply`), a page made with
its blocks (`create`), the trash, filing, folders, titles, PDFs, and the
conflicts the file won, written in the transaction of the ops.
`LocalRemote` writes this server's workspace in-process as the account
that made the vault, and asks before each write whether that account may
still edit there (`folder_links.may_edit`); `vault_http.HttpRemote` does
the same over HTTP for a folder of another server ("A vault on another
server").

**Opening.** The marker is checked (`vault_disk.check_marker`): none, or
another id, **stops** the vault (`status.stopped`); a directory that does
not answer is **offline** (`status.offline`). The marker file's file id
is kept in the state at the first round (`marker_file`), and another one
later, a backup disk given the vault's drive letter or a copy restored over
it, stops the vault too, until a relink takes the copy. Then the mark of the
vault's folder is read (a work folder's, below), and what the vault tracks:
its `.gammaignore`, else the default ("A work folder's round"). Then the
journal is settled (below), then the scan, and Gamma's temporary files
older than `STALE_TEMP_S` (60 s) are swept.

**The scan** (`vault_disk.scan`, `walk`) walks the directory with
`os.scandir`, one stat a file, and follows no symbolic link or junction;
nothing below one is read or written either (`vault_disk.path` refuses
it). It leaves out dot directories, `node_modules`, temporary names
(Gamma's `.gamma-*` and `*.gamma-sync-part`, Office's `~$*`, LibreOffice's
`.~lock.*`, macOS's `._*`) and `.gammaignore` itself, whatever the rules
say, then what the vault's `.gammaignore` leaves out (`Scan.excluded`, a
directory left out never entered; [work folders](work_folders.md)). Notes
(`.md`, `.markdown`) and papers (`.pdf`) are listed, pictures listed for
the links and counted, and the files of no class that the rules take
counted as untracked (the default takes none: it leaves them out). A link
or a junction, a name Windows refuses, and two names in one directory that
differ only by case, are reported (`status.refused`) and left, unless the
rules leave them out first (a link is decided as a directory). A cloud
placeholder (Windows' offline and recall-on-access attributes, macOS's
dataless flag, an iCloud `.x.icloud` stub standing for `x`, the rules
deciding by the name it stands for) is listed as one, never read and never
removed.
**A walk that fails anywhere, or a marker gone by its end, is offline**:
what it read stands for nothing, and never reads as files removed.

**A base** is a file's `vault_files` row: what the file was when the round
last took it, its hash, stat and file id and when they were read
(`read_at`), for a note its text and `nodes` (`[{id, g, m?, a?}]` in
document order: each block's id, its text in Gamma, for a highlight its
quote and page too, the memo of its links, its anchor) and `gone` (the
blocks the file dropped, `{id, hash, anchor, props, at}`, kept
`GONE_DAYS`, 30), the title and first filing pull recorded, the page's seq
the base is level with, whether pull made the file (`ours`), when a scan
first missed it, why it could not be written (`problem`), and `pending`.

**The journal.** Block and page ids are random, minted once, so a crash
cannot be made safe by deriving them. Before any effect a file's row holds
`pending`, what its base is about to be, under a token (`put_pending`);
after the effect it is promoted to the base (`promote`), or dropped when
the effect did not happen (`drop`). A push names its first op batch with the
token (`page_ops.batch_id`, `apply_batches(batch_id=)`), and the ids of the
blocks a plan makes start with it. A round that finds a pending settles it
by what the page and the file say. A push (`_plan_landed`) promotes when
its batch is in the page's log, else drops, so the file is read again. Its
pending records `at`, the seq the plan was made at, and `wrote`, what the
plan left on each block it wrote (`_wrote`: the text set or inserted, none
for a block deleted). Over HTTP the log is read from `at`, and the first
batch after it is the plan's if it landed (`commit_plan` writes it right
after that seq or not at all), so a log pruned down to its newest row still
says; this server's log is trusted only when it holds a row older than the
note's read (`read_at`), since pruning drops the oldest. Where the log
cannot say (the batch pruned, a 410), the page does: a block the plan made,
or each block it wrote holding exactly what it wrote, whatever Gamma wrote
on other blocks since, is the plan landed. For a page in Recently deleted
on another server, whose log is not served, the pending waits, and the push
that brings the page back settles it (`_settled_push`), so the note is
merged from the base the plan left and no word goes in twice. A new page
is there or not, or there without its blocks (a remote over HTTP makes the
page, then its blocks), and is then the note's
page all the same, its blocks merged into it from no base; a file pull
wrote holds the pending's hash, or is still the file the write put there
(the file id the journal named, or a text nearer the pending's than the
base's after a safe save: a person's save since, which the scan then
reads), or is that file moved; the bytes a write replaced, kept beside the
note until the history has them, go to the history; a file on its way to
the trash is where the pending says or where it was. Neither: the old base
stays, and the worst is a block read twice, never one lost. A pending that
cannot be settled yet waits for a later round, the vault going on. A plan is
written as the journal named it, never built again under the lock.

**Errors are a file's**, its row's `problem` and the status's `kept`, never
the vault's: one refused op does not stop the rest. Only the marker
(`Stopped`), the disk (`Offline`) and the account (`Refused`) stop a round,
which then counts as failed, so `RETRY_S` applies.

### Push

Push compares the scan with the rows (`Round._classify`, which reads and
hashes the new and changed files and writes nothing):

- **Changed**: a stat that moved, or the racy rule (`vault_disk.moved`): a
  file whose time is within `RACY_S` (2 s) of when its base was read counts
  as moved at the next scan, since exFAT and SMB keep coarse times and a
  save in the same tick would leave the same stat. Then the hash decides; a
  file of the base's bytes only takes the new stat. A note is read once it
  has rested `SETTLE_NOTE_S` (1 s) and statted again after the read (one
  that moved meanwhile waits); a PDF once it has settled, hashed in place
  (`linked_files.hash_file`) with its trailer's `/ID`. A note that is not
  UTF-8 is that file's problem.
- **Moved** (`vault_disk.pair_moves`), by evidence, in order: the same
  file id with the same size or hash, the same PDF `/ID` or a note's text
  alike (`ID_SIMILAR`, 0.3, with the id, never the id alone: ext4 gives a
  freed inode to the next file); a hash no other file on either side has; a
  PDF's `/ID` the same way; a note's text at least `MOVE_SIMILAR` (0.6)
  alike, word by word with front matter and marks left out, the name only
  breaking ties, at most `MOVE_COMPARES` (200) comparisons
  (`status.capped` says when the cap ended the search). File ids are looked
  at among changed rows too, so two names swapped are two moves, and a name
  changed only in case is a move whatever the text. A new note that pairs
  with a paper whose notes went from where they were is those notes moved,
  and so is one whose `gamma_id` names its page.
- **A directory renamed** (`_dir_renames`): gone from where it was and
  found elsewhere by its file id, else holding at least half of its files
  under the same names and sizes, is one folder renamed or moved and one
  rewrite of the rows below it (`vault_store.repath`), never a refiling per
  file. Where Gamma meanwhile moved the folder below the one the disk moved
  it into, the disk's places win.
- **New**, **missing**, **removed** (below). A file the scan leaves out for
  a fixed rule (a link above it, a case clash, a name Windows refuses) but
  that is there is never a removal; one there that the vault's
  `.gammaignore` leaves out is **deselected** ("A work folder's round").

Then, in order, the guard, directories, moves, changed files, new files and
removals.

**Removal needs evidence.** A path must be missing from two scans at least
`MISSING_S` (10 s) apart (`missing_since`), and is `lstat`ed again, with
the marker, just before it goes; a removal also waits while a file of its
kind waits to be read, which may be it, moved. A safe-save editor writes a
temporary file, deletes the note and renames: a scan can fall in the gap. A
note emptied (blanks, line ends, a byte-order mark) whose base had text is
held the same way.

**The guard** (`_held`): when the removals since the last quiet round, the
notes emptied counting and, in a work folder, each page whose files the
`.gammaignore` left out, pass `REMOVE_SHARE` (half) of the files the vault
had then, and it had `REMOVE_FLOOR` (10) or more, the round stops before it
writes anything, `status.held = {removals, total}`, until `?confirm=1`
(the panel's *Go on…*). A branch switched, a directory half synced by
another tool or a disk going away file by file look like this.

**Removed files.** A page all of whose files went goes to Recently
deleted, each row kept as a **tombstone** (`vault_store.bury`: its path the
place it would have in the vault's trash, `was` where it lived), written
before the page is trashed. The PDF gone and the notes left: the page stays
and reads a missing file. The notes gone and the PDF left: the plain blocks
the file held, unchanged since in Gamma, go (kept in the PDF's row's
`gone`), the highlights stay, and no notes file is made until the page
changes. A directory gone goes as a folder only when no page is filed in it
or below.

**New files.** A file that came back, a page in Recently deleted's or one
whose file went to the vault's trash, brings its page back
(`vault_disk.came_back`: where it lived with the same bytes or a text alike,
or anywhere with the same file id and a size or text alike; a hash alone
elsewhere is no evidence, since a template's copies share one). So a branch
switched and switched back loses nothing. Otherwise:

- a new note is a new page, titled by its stem or its front matter's
  `title`, with no `auto_title`, filed in its directory's folder (made where
  missing), its blocks, and its front matter read as the vault import reads
  it: `tags` (labels), `aliases`, `doi`, `authors`, `year` (the paper's
  record), and `parent`, which makes it a sub-page of the note it names once
  the round's new pages are made;
- a new PDF is a page reading it where it is (its document at `path`,
  named by the file's leaf), with the note it pairs with: the one whose
  front matter's `source` names it, else the one of its stem beside it,
  else one of its stem in `notes_folder`;
- a new PDF that pairs with a note already a page without a PDF is that
  page's file; a new note of a paper whose page has its PDF already is
  merged into that page from no base;
- a new note whose `gamma_id` (an older client's line) names a page of the
  vault with no notes file here is that page's notes, kept until a force
  decides ("Kept folders and the upgrade").

A file the remote refuses (a 413 behind a proxy's limit, a full quota)
keeps a row of no page with its stat and the reason, and is read and sent
again once it changes, never each round.

**A changed note** is parsed, matched with its base (`md_lens.match`, and
each block only moved, whose text is one base block's and no other's), each
block's text read into Gamma's (identity first), and merged into its page
(`vault_merge.merge_page`). The target starts from **the page as it is, not
from the file**, and takes only what the file changed since the base:

- a block whose text the file changed takes the change, merged word by
  word into what Gamma typed since (`file_wins`); where both sides touch
  one word the **file wins**, and Gamma's text is a conflict to record; a
  change the text holds already (a merge read again from the same base, a
  plan the journal lost) is made once, never doubled;
- a block the file moved (another parent, or out of the order it had among
  the siblings it still has: the longest run that kept its order stays)
  goes where the file has it, after the nearest block before it in the file
  under the same parent;
- a block new in the file is inserted the same way;
- a block the file dropped is deleted with its subtree, what the base does
  not name lifted to the nearest block that stays first; one Gamma changed
  since is a clash the file wins, and a new block of the file that reads
  like its base text is that block with its class changed, its id kept; so
  is a line the lens paired by its text with a block the file dropped
  earlier (`gone`) that the page no longer holds (a marker dropped, then
  typed again): the block Gamma changed or moved takes it, and the deleted
  one does not come back beside it;
- a block the base does not name stays wherever it is: Gamma's own blocks
  and every block with no span. A block deleted in Gamma comes back only
  when the file changed its text, never because the file still has it; one
  Gamma moved to another page stays there, and the file's change of it goes
  with it (`Merged.away`), its class changed too: a new block of the file
  that reads like its base text, as above, or a line the lens paired by its
  place alone with a block it reads less than `ALIKE` (0.6) like, which the
  moved block takes when the line reads at least that like its base text,
  so its text is never on both pages.

A block keeps its sibling key unless its order among the siblings it stays
with changed; the rest get keys between their neighbours'. The ops are
`sync_tree.diff(page, target, page_id, with_base=False)`: no `set` carries
a `base`, the merge being done. The merge is planned before the lock, the
plan named in the journal, and written under the lock while the page's seq
is still the plan's (`LocalRemote.apply(built=)`), in `MAX_OPS` chunks in
one transaction, with the `file_won` rows ("Conflicts") last; a page Gamma
wrote in between is planned again outside the lock, `PLAN_TRIES` (3) times,
then at the next round, so that what runs under the lock never grows with
the page. The page's labels take what the note's `tags` changed, Gamma's
own staying; a changed `title:` or `aliases` sets the page's. A page in
Recently deleted whose file changed comes back first: the file is the
authority. The base is then the file, and its seq the page's only when the
page says what the file says, front matter too, so that pull writes the
rest.

**PDFs.** A PDF with new bytes is its page's, read where it is (the disk
wins; `LocalRemote.replace_pdf`: its `linked_files` row first, then
`replace_attachment` with `at="path"` and `check_held=False`, the
highlights relocated). A library page's PDF filed in the vault keeps its
stored copy, and the copy pull wrote is followed: its first new bytes make
the page read in place. A PDF replaced in Gamma stands until the file
changes again.

**A vault that does not push everything** makes, merges and removes
nothing: its rows follow the files moved on disk, Gamma keeping its places,
a new file is read only to find a moved one, and a note gone or changed is
kept and reported ("changed on this computer and not pushed; a force pull
replaces it"). With push PDFs, a changed PDF still goes as above.

### Pull

Pull reads the cursor and the pages below the folder in one pass
(`layout`), and for each page whose seq passed its base's (and, once the
labels tree changed, each note with tags: a label renamed moves no page's
seq) writes it:

1. **Places.** A title that differs from the recorded title (never from
   the stem) renames the note, unless the note has a `title:` key, which is
   patched instead; a first filing that differs from the recorded one moves
   it. A PDF goes with the note only when it shares the note's stem beside
   it: one alone (`build/main.pdf`) is the disk's. A folder renamed or moved
   in Gamma renames or moves its directory (`_pull_dirs`), and the
   directories of folders gone from the vault go once empty (`_drop_dirs`;
   a link or junction in a directory's place stays).
2. **The PDF.** A page's stored PDF that the vault has no copy of (a page
   made in Gamma, a PDF attached to a page of notes), or that Gamma replaced
   since the copy pull made, is copied out (`_copy_pdf`), checked to be the
   stored file (`_fetched`: its hash, or a whole PDF; a sign-in page or an
   answer cut short waits), and followed from then on. A copy changed on
   disk is the next push's, or in a vault that does not push PDFs kept and
   reported until a force pull. A PDF of the disk's that Gamma replaced is
   that file's problem: pull never writes over it.
3. **The notes.** The page projected (`md_lens.project`), each block's text
   in the file's space (identity first, `vault_links` for a changed one), a
   highlight keeping or taking its anchor, then `md_lens.shape` over the
   base. `Unwritable` is the file's `problem` ("not written to disk" on the
   page) and nothing is written; the next change tries again.
4. **What a file cannot hold** as Gamma has it, a block split or lifted, goes
   back to Gamma as ops before the file is written, only where the vault
   pushes everything and no caret is on those blocks (`collab.editing`); a
   split that cannot go now waits, the file as it was, since written it would
   hold a block that is no block of Gamma's. A vault that only pulls writes
   nothing into Gamma, which is why pull is allowed in a clone or a Zotero
   workspace.
5. **The file must still be the base.** A note is small, so pull always
   hashes it. One changed since is pushed first and the page written again,
   once a round, where the vault pushes everything; elsewhere it is kept and
   reported, never pushed, and not written until a force pull. A cloud
   placeholder is never written over.
6. **The write** (`vault_disk.replace_note`; `write_new` for a new file,
   which never replaces a file that appeared meanwhile): the bytes written
   to a temporary name beside the note that the scan skips, flushed, the
   stat taken from the open file (a rename keeps the time, size and file
   id), the journal told, the old file kept by a hard link (a copy where
   there are none) so that the history keeps exactly the bytes replaced,
   then the rename, tried again while Windows says another program holds
   the note (`REPLACE_ATTEMPTS`); a last refusal is the file's problem and
   saves no base. Never `storage._rename_over`, whose same-size shortcut
   would drop a write. Bytes replaced that were not the base the round
   checked (a save in the moment between check and rename) are always kept
   in the history, and logged.

A new page's notes go where "What lands on disk" says. A file the disk
holds already, with the very bytes pull would write and no row (a vault
made again over the files it kept when it was removed), is taken as that
page's, never written beside it (`_take`). For a sub-page, pull also looks
where the gamma-sync client kept its files, `<parent's directory>/<parent's
stem>/<title>.md` (or `.pdf`) (`_beside_parent`): a file there that names
the page by its `gamma_id`, or holds those very bytes, is the page's, and
the directory is recorded as no folder. Pull never writes a file for a
page that did not change, nor a PDF it did not put there. A vault without
notes files (a folder kept before with its PDFs alone) writes PDFs only.

### Renames, moves and the trash

A file moved on disk, where the vault pushes everything, files its page in
the new directory's folder **in the same place of its filing list** as the
old one, and names the page by the new stem (a note with a `title:` key
keeps its title); a sub-page's file moved makes it a library page. The rows
move first, in one transaction (`vault_store.repath_files`: names swapped
go through names no file has), each holding a `moved` pending until its
page is filed and named, which a round that dies leaves to the next. The
rows record the filing, so pull never moves the file back.

A page trashed in Gamma, or one that left the vault (filed elsewhere,
deleted for good), moves its files to the vault's trash (`_left`,
`_to_trash`): the row becomes a tombstone at its trash path, written first,
then the file moves (`.gamma/trash/<UTC stamp>/<path>`, on the vault's
volume so that a delete is a rename). A PDF read in place is stored first
when its page lives on, its document at `store` with its hash and name
(`LocalRemote.store_pdf`). A note changed on disk that push has not read yet
stays where it is, kept, so that push brings the page back with it; a PDF
changed on disk whose page left stays where it is, no longer synced, and is
reported. A page brought back brings its files back from the trash under a
free name. A file is swept from the trash only once its page is purged and
the trash has held it `TRASH_DAYS` (30) at least, after any other page that
reads those bytes in place has them stored (`LocalRemote.spare`). Nothing
of a vault but its trash is ever deleted for good (`vault_disk.purge`).

### A work folder's round

A vault of a folder of this server that pushes everything is a **work
folder** ([work folders](work_folders.md)): its directory is the source, and
Gamma shows the files it chooses. A round is a work folder's when the
vault pushes everything and its folder carries the mark (`vault: {pull,
library}`), read from the folder's row by `remote.mark()` (`HttpRemote`
from the listing's folder tree; `Layout.work` carries it too) and kept as
`Round.work`. A whole round of
a work-folder link writes the mark again where the folder carries another
or none (a restore, a mirror's merge). A test that builds a `Round` on an
unmarked folder gets the round above, unchanged. In a work folder:

- **The layout** leaves out a page whose `work_folder` names another work
  folder than the vault's own (a library file of it filed here too).
- **New pages** are its files: `work_folder`, `file_name` (the file's leaf,
  the PDF's for a paper), `library: true` while the vault's switch says so,
  and the PDF's `doc.size` (`Round._create`, `_new_pdf`). The front
  matter's `parent:` makes no sub-page.
- **A move or rename on disk** (`_moved`) files the page first in the new
  directory's folder, whatever moved, then in its folders outside every
  work folder, and sets `file_name`; the title follows the stem only for a
  file not in the library (a library file's title is the paper's, free)
  whose note has no `title:`. `parent_page` goes.
- **The folder tree follows the disk.** The op path refuses a person's
  rename, move, delete or new folder below the work folder's root (its
  rule, [work folders](work_folders.md) "The op rule"), so `_pull_dirs`,
  `_move_dir` and `_drop_dirs` never follow Gamma there; `_drop_dirs` still
  takes a directory folder the vault dropped, but never one whose directory
  is there and left out by the selection. `folder_inserts(into_work=True)`
  makes a directory's folder below the root, where an import's walk never
  files.
- **Pull** moves or renames a file only for a note that follows Gamma (no
  document, where the vault pulls): the `.md` alone, never a PDF beside it,
  then `file_name`. Anything else's title and filing are only recorded. A
  note made in Gamma in one of its directories (the server stamps it) is
  written at `<dir>/<title>.md`, then named (`_file_named`, the rows' seq
  following where level). Pull copies no PDF out (`_copy_pdf` runs only in
  kept folders): a stored PDF filed here is reported "a PDF filed here in
  Gamma is not written into the directory".
- **The trash.** A note a person trashed where the vault pulls goes to
  `.gamma/trash` as above; any other file of a page that left stays where it
  is, its row an in-place tombstone (below).
- **A library file's versions** are stored as they come (`LocalSink.store`
  before `replace_attachment`), its document still at `path`; so are any
  file's while a comment thread on it is open (`feedback.kept.keeps_bytes`,
  the one predicate at the replacement; [feedback.md](feedback.md) "The
  commented version kept").
- **The feedback files.** After its sync a whole round writes the work
  folder's `.gamma/feedback/` (`folder_links._vault_round`, the summary in
  `status.feedback`; [feedback.md](feedback.md) "The files"), a held round
  too, unless the marker, the disk or the account stopped it; the hot loop
  writes them within a pass of a change ("When rounds run"). A paused or
  choosing vault writes none, and the files go with the vault
  (`_remove_vault`) and when it is let go (`_work_turn`): they are Gamma's
  own, deleted directly, never through the trash.

**The selection.** `.gammaignore` at the vault's root says what it tracks;
none is the default, notes, PDFs and pictures (`ignore_rules.DEFAULT_TEXT`).
`Round._read_rules` reads it once a round, a page's round too. The state's
`selection` holds the hash of the text the last whole round read (`""` for
none): a file gone since **stops** the vault ("its .gammaignore is gone: put
it back, or choose its files again") rather than reading every note and PDF
anew, and so does one that cannot be read; a hand edit is read as it is,
its removals through the guard. A work folder's round whose scan selects
more files than the state's `cap` (`selection.MAX_FILES`, 20,000, or the
files tracked at the takeover) stops before it writes, `status.too_many =
{files, cap}`. A marker from before with `ignore` globs and no
`.gammaignore` gets one written at the first whole round: the header, the
default, then each glob as an exclusion (one with a `/` anchored at the
root); later rounds read only the file.

A file the selection leaves out while it is on disk is **deselected**
(`_classify`, `_apply_deselections`):

- in a work folder, a page whose files all left goes to Recently deleted
  first (`deleted_by_vault`), then each row becomes an **in-place
  tombstone** (`vault_store.bury_in_place`: `path` is `KEPT` + the path,
  `was` the path, no pending): **the file is never moved**. A round that
  dies between the two leaves a live row the next deselects again, or a
  tombstone of a live page, which `_restore_file` sends back. One of a
  page's two files left out (a hand edit; the dialog keeps a paper's two
  together) is forgotten, the page keeping the other, and reported ("left
  out by .gammaignore; its page keeps its other file").
- in a kept folder, whose source Gamma is, the row is forgotten and the
  page stays.

An in-place tombstone is listed with the others but never seen by
`_recover_trash` (it has no pending) and never purged. Its file tracked
again (`_push_new`, before `came_back`) is that page back whatever its
bytes, nothing moved (`_unbury`). Its page live again while the file is
still left out (the mirror, a crash) goes back to Recently deleted
(`_restore_file`): a work folder's page whose file is untracked is there.
Once the page is purged the sweep forgets the row, a PDF stored first for
any other page that reads those bytes in place (`spare`), as for one the
trash takes.

**Pull respects the selection** (`Round._tracked`): a new note, a rename or
move's target, a picture a note links, a PDF copied out, a file restored
from the trash, and a file pull would take as a page's (`_older_note`,
`_take`) are written or taken only where the rules track the path. One left
out is not written, its page reported ("not written: .gammaignore leaves
out {path}; tick its folder whole in Choose files…"), and tried again next
round.

**The takeover.** A work-folder link from before work folders, one made
of a folder that already held pages, and a kept folder whose push was
turned to everything are taken over at their first round as one,
before the round, by `takeover.take_over` (the mark, then each page of its
layout stamped quietly, filed by its row's directory first, sub-pages
flattened), its state's `work_folder_at` the commit point;
`takeover.release` lets one go whose push came down. Both are in
[work folders](work_folders.md) "The takeover".

### History

`vault_history` keeps the bytes Gamma's writes replaced: one entry a path
every `HISTORY_EVERY_S` (5 minutes) at most, the first bytes replaced in
that time, so that what the file was before a burst of Gamma's writes is
kept; bytes that were a person's save are always kept. Entries go after
`KEEP_HISTORY_DAYS` (7), and the oldest while the vault passes
`HISTORY_BYTES` (32 MB compressed). A round tells a person's save by the
bytes it replaced differing from the base. Gamma's file editor
(`folder_links.write_note`) tells one the same way: bytes it replaced that
are not the ones its `hash` named (a save between its check and the
rename) are always kept. No route reads the history yet.

### Force

`force=pull` makes every file its page, where the file is now: the rows
first follow the disk's directory renames and moves, Gamma keeping its
places, so Gamma's text is written where the note is, never again beside
it. `force=push` makes every page's blocks with a span its file's, and is
pushing everything, refused where that is (`folder_links.forced`). Both
record what they replaced as `sync_conflicts` rows written resolved, a
block a force push deletes kept in its base's `gone` with its properties (a
highlight's quote, page and rectangles), so a force leaves a record. The
app counts and confirms first, and only the vault's maker may ask.

### When rounds run

- **The tick** (`folder_links.tick`, every `TICK_S`, 30 s, in the app's
  periodic loop): a vault of this server has a whole round each tick, its
  scan being the truth at a stat a file; a vault of another server a whole
  round when that server's change feed moved past its cursor (asked at most
  every `REMOTE_POLL_S`, 60 s, waited on at most `ASK_TIMEOUT_S`, 15 s),
  else, when it pushes, a round of push alone, which asks that server
  nothing while the disk is quiet; a vault of one file when its file's stat
  moved (`gamma_sync.moved`). Never synced, or failed `RETRY_S` (5 minutes)
  ago or longer: a whole round. A link is resting, and left out, while
  paused, while its round runs, or within `RETRY_S` of a failure. A
  remote vault's round runs in a thread of its own, so a slow server holds
  up nothing else; a link whose workspace here is gone is forgotten, its
  directory as it is; then the linked files' sweep. One tick runs at a time,
  and one asked for meanwhile runs again after it.
- **The hot loop** (`folder_links.hot_tick`, every `HOT_S`, 2 s): a page
  of a vault of this server gets a round of its own (`vault_sync.sync_page`)
  once `HOT_S` passed since its **last** write in Gamma (trailing: an
  `ops.commit_listeners` entry marks it and does nothing else, and a
  vault's own writes and the folder and label trees mark nothing), and a
  page open in a window (`collab.open_pages`) whose file's stat moved since
  its row and since its last look. A page's round has no scan: it pushes
  the page's own files when their stat moved and pulls the page, removes
  nothing, makes or moves nothing in a directory the disk changed since the
  last scan (`_steady`, by its file id), and keeps no state. A mark stays
  until a round covered its page: a vault whose round runs leaves it for
  the next tick, as does a file still being written. The same tick then
  writes the feedback files of the work folders a comment write, an op
  commit or a Send marked `HOT_S` ago or more (`feedback.files.due`,
  `_feedback_pass`, under the link's lock): a lock a round holds keeps the
  mark for the next tick, as does a pass that left a file a reader held or
  pictures past its cap; a paused, failed or choosing link, or none, drops
  it, its next whole round writing ([feedback.md](feedback.md) "The
  files").
- **The hint** (`POST /hint`): a window of the app got the focus; a tick
  runs now, in the background. There is no file watcher: the tick, the hot
  loop's stat of open pages and the focus cover it.
- **Asked**: a vault made, synced from the panel (`POST /{id}/sync`),
  resumed or relinked runs a round in a thread of its own
  (`run_in_background`), and a sync asked to wait (`?wait=1`, for scripts)
  runs it inline. `run_in_background` takes the vault's round lock and
  writes its `running` and `started_at` before it returns (`_take`), so
  Sync now's answer already says the round runs and no request is held
  open for it: the desktop app reads the vault (`GET
  /api/folder-links/{id}`) until `running` comes down and then says how the
  round went.

One round runs per link at a time (`_locks`), and a vault reads `running`
only while its lock is held (`_round_held`, applied wherever a row is
read): a round reads running however long it takes, and a mark a server
stopped mid-round left reads not running, so the vault is due again at
once and a client following it stops. A round asked for while the lock is
held (a round, a page's round of the hot loop, a write from Gamma's file
editor, a relink) is kept and runs once the lock is let go (`_again`),
requests made meanwhile adding up so that none loses a force or a
confirmation another asked for; `wait=1` waits up to `ROUND_WAIT_S` (30 s)
first. From the moment it is kept until it ends, the vault reads `running`
(`_again`, then `_starting` until the kept round has marked itself), so a
client following Sync now never takes the round before it for its own. A round marks
each page it wrote (`collab.publish(ws, page, {"t": "vault"})`), and the
page's chip reads its standing again. A paused vault (`PATCH {paused}`,
`status.paused_at`) is left out of the tick and the hot loop, its feedback
files waiting until it syncs again; a sync asked for still runs it, and a
round already running finishes. The pause is the user's: a round's status
never overwrites it.

A folder vault's `status`: `{running, started_at, last_sync, last_error,
failed_at?, counts, kept, untracked, refused?, held?, stopped?, offline?,
shared?, capped?, upgraded_at?, work_folder_at?, too_many?, push_off?,
feedback?, paused_at?, progress?, folder_path}`, with `counts` `{added, updated,
renamed, removed, kept, unchanged, sent, waiting, read, conflicts}` and
`kept` the first
`KEPT_SHOWN` (50) files kept, `{path, why, force}` (`force`: a force pull
would replace it). `shared` lists the pages filed in it that an older
vault beside it keeps. `feedback` is a work folder's feedback files as the
round left them, `{open, left?, error?}`: the open threads, what waits for
the next pass, or why nothing was written ([feedback.md](feedback.md) "The
files").

While it runs, a folder vault's round writes how far it is into the status
as `progress` (`folder_links._progress_writer`, from `Round`'s `progress`
callback, `_report`): `{done, total, bytes: {done, total}, path}`. It
counts the files push takes into Gamma (the changed and the new, a PDF
with the note it pairs with counting two), then every page of the folder
that pull looks at, each side joining `total` once that side knows them;
`bytes` are the PDFs push takes in, by the scan's sizes, and `path` the
file at hand (a page with no file yet, its title). It is written once the
round has run `PROGRESS_S` (1 s), then at most every `PROGRESS_S` and once
all it counted is done, beside the `running` and `started_at` the round
began with, so a quick round, as the tick's round of a quiet vault is,
writes none. The round's own status at its end has none, and a row read as
not running drops one a stopped server left. The desktop app's sync panel
shows it as *Syncing 30 / 120*, the PDFs' bytes and the file at hand in
its tooltip ([desktop architecture](../../desktop/docs/architecture.md#the-sync-panel)).
A page's round of the hot loop and a file vault's round write none.

### A vault of one file

A file vault's round (`folder_links._file_round`) is one `gamma_sync.Push`
of its file in mode `file`, through `LocalSink` (this server's workspace,
as the vault's maker) or `gamma_sync.RemoteSink` (another server, with a
token that may write), and nothing else: no marker, no scan, no pull. What
it knows of the file is the row's `state`, `{doc, size, mtime, seen?,
title}`.

**Identity decides.** A stored PDF's hash (its page's `doc.hash`) is the
first 24 hex characters of its SHA-256, so `gamma_sync.doc_of` hashes a
file and knows without a byte sent whether it is Gamma's file. A sink's
`current` answers the hash of the document the page owns (none for a
duplicated page, whose `doc.copy` shows another's file) and that
document's name. A file is looked at only when its size or time differ
from what is known (`modified`). Then:

- Not **settled** (`settled`): written less than `SETTLE_S` (3 s) ago (a
  time ahead of this clock counts as long ago), not open for reading
  (Windows refuses while its writer holds it), or without `%%EOF` in its
  last kilobyte. It *waits* ("still being written", "not a whole PDF yet")
  and the next pass asks again. A file at rest that does not end as a PDF
  ends also records its stat as `seen`, so it starts no round until it is
  written again; a file still being written or locked does not, since a
  lock can end with no new stat.
- The same bytes as known: the stat is taken, nothing travels.
- On this server, the maker no longer an editor of the workspace: *kept*,
  "you may no longer edit this workspace", asked before the page is read
  (`LocalSink.current`), so it learns nothing of the page.
- The bytes the page shows already: the stat is taken, nothing travels.
- The page gone: *kept*, "the page is no longer in Gamma".
- Otherwise the file is **sent**: stored, then the page's PDF replaced by
  it under the name of the page's document, or the file's name; a page
  that read its PDF in place goes on reading it in place. A file
  whose bytes or stat changed while it was stored waits for the next pass
  instead (`gamma_sync.Changed` over HTTP), without `seen`.

A failure is the file's, never the pass's: a refused token, a full quota,
bytes another page carries (the replacement's 409), a page that is gone, an
answer that is no JSON (a sign-in or captive portal's page) or one cut
short make the file *kept* with the reason and record its stat as `seen`;
`moved`, the tick's test, compares with `seen`, so such a file starts no
round until it changes. The round then fails the link with that reason, so
`RETRY_S` applies and the next round tries again. A missing file waits
("the file is not there"). Its status is `{running, started_at, last_sync,
last_error, failed_at?, counts: {sent, waiting, kept}, waiting, last_sent,
title, relocation_error?}`.

**The replacement** is the one `PUT /api/pages/{id}/attachment` makes
([api.md](api.md)): the page keeps its id, notes and highlights, the
highlights are stamped with the file they were measured on, the relocation
run starts ([highlight_relocation.md](highlight_relocation.md)), and the
open viewers switch. Every PDF push, a file vault's, a folder vault's or a
linked file's, asks `keep_old_days` of 1 (`gamma_sync.KEEP_OLD_DAYS`): the
file the page let go of is purged a day after nothing references it
instead of thirty ([user_db.md](user_db.md#stored-files)), unless a
highlight the run left matched from it names it as its reference, or
another page still carries or shows it. A run that cannot start reports
`relocation_error`, and the replacement stands.

## Conflicts

A clash the file won is a `sync_conflicts` row of kind `file_won`
(`vault_sync.CONFLICT`), written in the transaction of the push's ops, in
Gamma's space: `mine` Gamma's text, `theirs` the file's, `result` what the
block took (the file's), `base`. The same open clash is not written twice
for one block. No conflict copy is ever written into the vault: the file
holds the file's text, Gamma's waits in the row.

The vaults' rows are kept apart from a mirror's and a Zotero link's
(`sync_engine.VAULT_KINDS`, `NOT_VAULT`): `list_conflicts` and
`resolve_conflict` take `kinds`, the vault's routes read `file_won` only
(`folder_links.conflicts`, the vault's own pages), and the mirror's and
Zotero's reads, counts and deletes leave them out. `GET
/{id}/conflicts?page=` lists the open ones; `POST /{id}/conflicts/{cid}`
`{choice: keep | mine | theirs}` resolves one as the vault's maker: `mine`
writes Gamma's text back into the block, which the next pull writes to the
file, `theirs` the file's, `keep` only marks it seen. A force writes its
rows resolved, a record of what it replaced. A vault of another server
cannot write that server's table: Gamma's side of a clash goes to this
computer's vault history instead ("A vault on another server").

In the page, the chip's card counts the open conflicts (`page_vault`), and
the merge chip reads them: App's `loadMerges` asks the vault's route as
well as the mirror's when the open page has a vault with conflicts, and
`MergeResolver` shows a `file_won` row with the sides *This file* (on this
computer) and *Gamma*, posting the choice to the vault's route.

## Rules that keep files safe

As built:

- **The marker** is checked before and after each scan; a round never makes
  the root; a copy of the vault at its place stops it.
- **A partial walk is offline**: any error of the walk, or the marker gone
  by its end, and nothing reads as removed. A disk unplugged mid-round is
  offline, never its files gone.
- **Removal needs evidence**: two scans `MISSING_S` apart, an `lstat` just
  before, no file of its kind waiting to be read; a note emptied is held the
  same way.
- **The guard** holds a round whose removals pass half of ten files or more
  until the user says go on.
- **Tombstones and the trash**: a deleted page's files go to `.gamma/trash`,
  never deleted until the page is purged and 30 days passed, and a file come
  back brings its page back.
- **A work folder never moves a file the disk made**: a file its
  `.gammaignore` leaves out, and a PDF whose page left, stay where they are,
  their rows in-place tombstones with no pending, so no crash recovery moves
  or trashes them; the page goes to Recently deleted before the row is
  buried, and either half alone heals at the next round. A `.gammaignore`
  gone stops the vault; pull writes no path the selection leaves out.
- **Pull writes only over the base**: always hashed first; a file changed
  since is pushed first or kept. Pull never writes a page that did not
  change, nor a PDF it did not put there, nor over a cloud placeholder.
- **The write** never loses a save: temporary file, flush, the old bytes
  kept by a hard link, rename with retries; a save in the moment between a
  round's check, or Gamma's file editor's, and its rename goes to the
  history.
- **The journal**: before every effect, settled at the next round; at worst
  a block read twice, never one lost.
- **Shape never removes an id**, and the meaning check refuses a write that
  would change how another app reads a block the change left alone.
- **No link is followed**: the scan follows no symbolic link or junction,
  and no round reads or writes a path below one, nor removes a link or a
  junction found where it recorded a directory (`os.rmdir` would delete a
  junction whose target holds files).
- **The file wins** a clash, and Gamma's text is kept as a conflict.
- **Pull-only is a promise**: a vault that does not push writes nothing into
  Gamma; what changes on disk is kept and reported until a force pull.
- **One replacer per file**: a vault's PDF is its round's, a reader's the
  worker's.
- **Refusals** are one function, `folder_links.check_vault`, asked when a
  vault is made or changed and before each round (a refused round is
  `status.stopped`). It refuses: a place that is Gamma's data directory,
  inside it or around it, by spelling or by identity (the device and file
  id of the path and each directory above it, so a `\\?\` device path or
  an administrative share `\\host\C$\…` counts); a directory vault inside
  or around another's; a file vault inside a directory vault (open the
  page from there instead); a marker another live link holds (a copied
  directory); pushing everything into a clone or a workspace with a Zotero
  link (pulling, and pushing PDFs, are allowed there); and a folder that
  holds pages another vault of the workspace pushes into, when both push.
  For vaults of this server the folders overlap by the folder tree; for two
  vaults on this computer of one workspace of the same other server, by the
  folder ids, else the names from the top their rounds recorded
  (`_named_overlap`, `status.folder_path`), two folders of one name beside
  each other reading as one. A vault of another server is asked no marker
  or clone rule, and a vault on another computer pushing into the same
  folder there cannot be seen from here.
  A round asks only of the links made before it, so of two vaults that came
  to overlap the older goes on. A mirror or a Zotero link is refused for a
  workspace a vault pushes everything into (`pushing_vault`), and *Publish*
  for a page a vault keeps.
- **Making a vault**: the path is a full one (`clean_path`: not relative,
  not a drive's root; `clean_file_path`: a `.pdf` that is there), unique
  among the links ignoring case. A workspace has at most `MAX_LINKS` (10)
  folder vaults and `MAX_FILE_LINKS` (50) file vaults. A vault made from a
  folder that pushes everything refuses a folder with pages and a directory
  with notes or PDFs both (`_merges`): make it from an empty side. A vault
  made from a directory (`new_folder`) gets a new folder at the library's
  top named after it, even when one of the name exists. A directory holding
  a marker no link holds was a vault before: refused unless `adopt`, which
  keeps it. A directory vault takes in the file vaults of its workspace
  inside it that its own maker made (`_take_in`, `_takes_in`: taking one in
  removes its link, which only its maker may; around another account's it
  is refused): each page keeps its id and all it holds, is filed
  first in the folder of its file's directory, its other filings kept, its
  file the vault's PDF row, and its own link goes. The folder and the marker
  a vault makes are taken back when its row cannot be stored.
- **The maker**: what changes what reaches the disk or leaves it (`pull`,
  `push`, `notes`, a token, a force, a confirmation, a relink, a removal) is
  the account that made the vault's alone, since the directory is on its
  computer; another editor edits the pages. The maker keeps the vault
  whatever its role becomes, a viewer or no member at all: it sees the
  vault and its path, in the list (`visible_links`) and in its pages'
  chips, and may pause it, sync it, read the removal counts, relink and
  remove it; its conflicts stay a member's. It learns nothing new of a
  workspace it left: a round of its vault there fails at once with
  `vault_sync.NO_ACCESS`, before `check_vault`, whose reasons read the
  workspace as it is now and name other vaults' places, and the folder's
  names stay the last ones (`_folder_names`). A maker who may no longer
  edit the workspace, a viewer now, is told only that its vault is
  refused (`REFUSED_UNSAID`), in its round's status and in the answer to its
  own change, relink or force alike: `check_vault` words a refusal so for a
  vault of this server whose maker is no editor there. A file vault's push asks
  whether the maker may edit before it reads the page
  (`LocalSink.current`), so a viewer or a leaver whose file changed is told
  only that it may no longer edit, whether the page still stands or holds
  those bytes already. A member who may not act on a
  vault sees no path of it, and no text a round wrote, which may name one:
  of the status, `push_off` is then a fixed sentence. The routes that take
  a path want the shell's secret ("The routes").
- **Removing the vault** (`delete_link`) leaves its pages as they are. The
  PDFs and pictures it reads in place are stored first (`GET /removal`
  counts them and their size), and one that cannot be stored stops the
  removal before anything is undone; its live pages' documents are then at
  `store`, and a page in Recently deleted reads in place still, its copy
  stored (`_store_in_place`). Then, with `files=keep`, the marker
  goes and the files stay; with `files=trash`, the files its pulls made and
  nobody wrote in since (`ours`, still as their base) move to its trash
  first, and only the marker goes, never the trash. A vault whose disk is
  not there keeps its pages reading their files, missing, to be located. A
  file vault's file is never touched.
- **Relink** (`POST /{id}/relink`): a vault's directory moved or its disk
  mounted elsewhere is found again only where the marker is this vault's;
  the PDFs it reads in place, and any opened page that reads one of them,
  are read there from then on.

## Kept folders and the upgrade

A folder kept on disk before synced vaults is a folder link whose `state`
names no marker (`folder_links.older`), its directory holding the
gamma-sync client's state file (`.gamma-sync.json`). Migration step 50
([migrations.md](migrations.md)) gave `folder_links` its `pull` column (1;
0 on file links) and let `push` say 2. Its first round takes it onto the
engine (`vault_upgrade.upgrade`), as a vault that pulls, with push off or
PDFs as its arrow was and notes files as it had them; push may then be
turned on for everything. Four steps, each left as it was or done by a kill
at any point, the row's state being the commit point:

1. The marker written, or one an earlier try wrote taken when no other link
   holds it: `.gamma/` is all a round writes beside the files, and the
   status says so from then on (`upgraded_at`).
2. The state file's files and directories into vaults.db in one
   transaction (`vault_store.take`), each file with the stat the client
   recorded or, when it is as recorded (the size, and the time within a
   second), its own. A PDF keeps its page and identity (`ours`); one changed
   on disk whose page's PDF Gamma replaced too is kept as the client kept it
   (`BOTH_PDF`) until it changes or a force decides. A note gets a base
   that names none of its blocks (`vault_sync._unknown`): one as the client
   wrote it keeps its hash, so the first pull writes it once more by the
   lens, its old bytes to the history, and its base is known from then on;
   one changed on disk keeps none, so it is kept and reported (`UNKNOWN`)
   until a force pull or a force push decides. A picture under
   `attachments/` keeps its upload name. Each folder's directory that is
   there is recorded, by the client's naming rule. A cloud placeholder is
   taken by its stat and never read (a note's hash waits for the first pull
   that finds its bytes, `UNREAD`). A file not there is not taken (pull
   writes it again), but a pass during which the disk went is offline, as a
   scan's is. A directory whose state file is gone has its files taken by
   what they say: a note by its `gamma_id`, a PDF by its bytes, the walk
   reading what the directory's `.gammaignore` tracks (one that cannot be
   read stops the upgrade). No title or
   filing is recorded, so pull renames and moves nothing until Gamma
   changes them.
3. The row's state names the marker.
4. The state file goes. A kill just before, or another program holding it,
   leaves it for the scan's sweep, which takes a `.gamma-sync.json` at the
   vault's root as one of Gamma's temporary files, and for the removal,
   which takes it first.

A force push of such a note pairs its blocks with the page's as pull would
write them, an older client's highlight read as it wrote one. The
`gamma_id` line those notes carry is read where a file has it and its row
does not say which page it is, and is never written again. Two folders
kept before that overlap and both pushed PDFs, which the old links allowed,
cannot both push now: the newer one's push is turned off at its upgrade and
the status says why (`push_off`). A link made now over a directory an
earlier link kept the same folder in, of the same server, goes on with it
as an older one (`_kept_before`); one holding another folder's files is
refused. Removing an older folder with `files=keep` leaves its directory,
state file and all, for a link made there again; with `files=trash` it is
taken onto the engine first, so the removal knows which files the client
wrote.

## A vault on another server

The desktop app keeps a folder of another Gamma server, a NAS say, as a
vault on this PC with no clone: the link on this computer's host server
names that server (`remote_url`), a token of it (`token`, sealed with the
data directory's key like the mirrors' tokens) and the token's id
(`token_id`, for whoever revokes it there), and `workspace_id` is the
workspace there. Its rounds are the same engine's through `HttpRemote`,
which speaks to that server with the token through the routes the browser
uses: the change feed for each page's seq and the cursor, the listing of
the vault's folder (`GET /api/blocks/root/children?within=<folder>`: its
pages whether the library lists them or not, their filing and the folder
and label trees; a server that knows no `within` lists the library, read
the same), a page's subtree, a block, a stored file; ops for the folder
and label trees, a page made (`POST /api/pages`, then `PUT
/api/blocks/{id}/children`), the trash and a restore, uploads and the
attachment replaced, each of these API requests naming the vault's op
client in `X-Gamma-Client` so that the work-folder rule there reads the
writes of routes with no client in their body as the vault's (a file's
upload and download go without it); and one route of its
own, a page's plan (`POST /api/pages/{id}/plan`, `ops.commit_plan`). A
token that may read pulls; pushing takes one that may write, of an editor.
A vault of another server is never a work folder itself (it writes no
mark), but one that pushes everything into a folder there that carries a
mark runs a work folder's round, as the op rule there asks.

What differs from a vault of this server:

- **No write lock.** A plan is written as the page stood when it was read
  again just before: one request, which that server writes in one
  transaction or not at all, refused whole (409 `stale`) when the page's log
  moved past the plan's seq. Whatever someone typed there in between is seen
  by the plan made again, never written over or deleted unseen. A batch id
  the log holds already is answered from it (a retry whose answer was
  lost). A server older than this app has no plan route: the vault waits
  until it is updated.
- **Conflicts.** The file wins, as here. Gamma's side cannot go into that
  server's conflict table, so it is kept in this computer's vault history
  under the page's notes before the plan goes, and counted in
  `counts.conflicts`.
- **PDFs are stored there**: a new paper's bytes are uploaded before its
  page is made (its document at `store` there, never `path`), a new
  version's before the attachment is replaced, and a picture a note links
  is an upload there too.
- **Not seen from here**: the carets in that server's rooms (a shape op may
  land on a block someone is typing in, which that editor merges), and the
  relocation run, which a token cannot start: the highlights a callout
  brings with no anchor wait for *Find highlights in this PDF* there. A
  vault on another computer is not seen either: it may push into the same
  folder there. The vaults on this computer of that workspace keep the
  rules of this server's: two that overlap and both push are refused
  (`_named_overlap`), and the layout leaves out the pages of another such
  vault that may take them, by `vault_sync.left_out` over that server's
  folder tree, reporting the shared ones. The hot loop does not run for it.
- **The account.** That server not answering, answering with no JSON a
  success, a 5xx, a 404, 408 or 429 (a sign-in portal's page, a proxy with
  no server behind it), or a read with a 5xx in JSON, is `Unreachable`; the
  token refused, or no editor's where it writes, `Refused`: the vault
  waits, never a file's problem. Any other refusal is the file's: a 404 in
  JSON, a write's 5xx, a proxy's 413 on a body past its limit among them. A server's
  answer that echoes the token has it left out (`gamma_sync.scrubbed`).
- **The data format.** That server's pages must have this one's shape: its
  change feed names its workspace data format (`format`,
  `migrations.workspace_format`), and one that differs, an older or a newer
  release, is `Refused` (`HttpRemote._check_format`, asked once before the
  remote's first request of any kind: a page's round of the hot loop pushes
  before it reads a listing) before anything is read or written, with the
  mirror's sentence
  (`sync_engine.FormatMismatch`: "sync is paused until … is updated"). The
  vault waits until both run releases that agree, as a clone does
  ([mirror.md](mirror.md)). Format 52 is one such change: a page's
  document became one property, `doc`.

**The token.** It is checked on creation (`/api/sync/whoami` names its
workspace, which must be the request's `workspace` when one is given, and
its scope; the folder must exist there, a page through the plain block
read), waiting at most `ASK_TIMEOUT_S`. Read scope is enough for a vault
that does not push; a vault that pushes and a file vault take a write token.
Turning push on (`PATCH {push, token, token_id}`) checks the write token
given and keeps it in place of the link's own; without one, the stored token
must write. `PATCH {token}` alone replaces a token revoked there. The token
is never answered back, expires as its issuer set it (the desktop app mints
a year), and stays on that server when the vault is removed: whoever minted
it revokes it, as the desktop app does. The vault is its maker's account's
alone: only that account lists, changes and removes it. A vault of another
server is made from its folder (no `new_folder`, no `adopt`), and a
directory of it reads no PDF in place: there is nothing on that server to
store first when it goes.

A file vault of a page on another server pushes through
`gamma_sync.RemoteSink`, the client's own three calls ("The client").

## The desktop app

The shell's part is in [desktop/docs/architecture.md](../../desktop/docs/architecture.md).
In short, from `desktop/main.js`: the shell starts its local servers with
`GAMMA_FOLDER_LINKS=1` and a secret new at each start (`GAMMA_SHELL_SECRET`,
`lib/sidecar.js`), and sends it as `X-Gamma-Shell` on the routes that take
a path, only to a local server of its own (`shellWord`). The ways in are a
folder's *Sync with this computer…* and the panel's chooser
(`addFolderVault`), *Add a synced vault…* in the launcher and the panel
(`addDirectoryVault`, which needs no open server: the host's personal
workspace unless a local server's workspace is open), and a page's *Push
from a file on this computer…* (`addFileVault`). The page asks by posting
`keep-folder-on-disk` (*Sync with this computer…*), `follow-file-on-disk`
(*Push from a file…*), `reveal-on-disk`, `relocate-file` or
`carry-to-server` to itself, which the page preload passes on
(`desktop/preload.js`); the first two keep their names from before synced
vaults, the only ones a desktop app released before them passes on. The
shell asks the user with its native picker. The vault dialog is a view of
the sync panel (`desktop/ui/bar.html`): the directory, what `POST /preview`
found there, the switches *Pull* and *Push: off / PDFs / everything*, a
sentence saying what the first round does, *Sync* and *Cancel*. A vault of
another server keeps Pull on and pushes PDFs at most: its view's Pull
switch cannot be turned off, and the shell refuses both before sending
anything. The panel's rows and the sentences the shell says are read by
`desktop/lib/keeping.js`, which keeps the vocabulary; an up-to-date row
also counts the names the scan refused among those not synced, and says
*push turned off* (`push_off`) and *n in another vault* (`shared`), with
the reasons, titles and `upgraded_at` in its tooltip. A window's focus
posts `/hint` to the running local servers. A folder of another server is
kept on the host with a token the shell mints there. *Open with* and
*Server → Open a PDF…* go to `POST /open` on a local server (`readPdf`),
never a remote one; where the window's workspace takes no reader page (a
remote server, a clone, one the account only reads), the shell first
hashes the file and asks that workspace for a page of those bytes (`GET
/api/blocks/by-doc/<hash>`, only the hash sent), and opens it there.
*Another server…* (`carry-to-server`) is the opener's alone: the shell
refuses it unless `reader.mine`. Once the page is there, the page here goes
to Recently deleted by the digest the shell read before its export, never
deleted for good ("To another server" above).

## The routes

`/api/folder-links*` (`routers/folder_links.py`): the full table is in
[api.md](api.md); in short:

| Route | Does |
|---|---|
| `GET ""`, `GET /{id}` | the vaults the account may see, with their direction in words and status |
| `POST ""` **P** | a vault: of a folder, of a directory into a new folder, of one file, of another server's folder or page |
| `POST /preview` **P** | what making a vault there would find and write, writing nothing |
| `PATCH /{id}` | `notes`, `pull`, `push`, a token, `paused` |
| `POST /{id}/sync` | a round: `wait`, `direction`, `force=pull\|push`, `confirm` |
| `POST /{id}/relink` **P** | the vault found at another place |
| `GET /{id}/removal`, `DELETE /{id}?files=keep\|trash` | what removing would do; remove |
| `GET /{id}/conflicts`, `POST /{id}/conflicts/{cid}` | the vault's `file_won` rows |
| `GET /page/{id}` | a page's standing: `{vault, reader}` |
| `GET`, `PUT /page/{id}/file` | the page's notes file, for Gamma's file editor |
| `POST /hint` | a tick now |
| `POST /open` **P**, `POST /opened/{id}/add`, `POST /opened/{id}/locate` **P** | the reader |
| `GET`, `POST /opened/{id}/carried` | the digest of an opened page before a carry to another server; the page let go here after it (409 when it changed) |

A signed-in account's session only, never an integration token or the
guest. Listing, one vault, its conflicts and a page's standing take any
member; the rest the editor role, and what changes what the disk sends or
takes the vault's maker. The maker passes whatever its role now, none
included, for one vault, a pause, a plain sync, the removal counts, a
relink and a removal; a vault's conflicts take a member. **P** routes take a path on this computer: a page
of Gamma never names one, the shell's native dialog does, so when the app
started the server with a secret (`config.shell_secret`), a request without
it as `X-Gamma-Shell` gets 403.

**Gamma's file editor** (*Edit the file*): `GET /page/{id}/file` reads the
page's notes file whole, `{text, hash, path}` (an editor's, and of a vault
that does not push everything its maker's alone); `PUT` writes it as a
person's editor would (`folder_links.write_note`): only in a vault that
pushes everything, the file still the bytes `hash` names (409 with what it
holds now), the text one Gamma can hold (`md_lens.parse`, no block past
`ops.MAX_CONTENT`), through `replace_note` with its history, under the
vault's lock, then the page's round is marked to run at once. The page
takes the file at that round, as it takes any edit made on disk. A notes
file that is a cloud placeholder is neither read nor written: both answer
400 (`_not_in_the_cloud`), as a round leaves one alone.

**A page's standing** (`GET /page/{id}`): `vault` is `{id, name, scope,
path, pull, push, problem, conflicts, paused}` for a page a vault keeps
(`page_vault`; the vault that keeps a page held by two is the one the
engine gives it, a pushing one first), `problem` the vault's own reason
first (`_halted`, in fixed words: it stopped, its directory is not there,
it is held, its last round did not finish, its followed file is gone),
else why the page's file was not written or read, which the chip shows as
*Not written to disk: …*; `reader` is `{path, missing, mine,
relocation_error?}` for a PDF read where it is, `mine` whether the asking
account opened the file. `path` is empty for a member who is no editor,
but the vault's maker. The page reads it as it opens and on its socket's
`{t: "vault"}` (`useVault.js`). `mine` decides what *Add to library…*
offers (`vaultState.openedHere`, `carriesToServer`): for another account's
opened file, neither another workspace, another server nor keeping it
pushing (`FolderPickerDialog`'s `opener`). A folder vault that pushes
everything and does not pull locks the page's notes: they have no editor,
and a change that would make, remove or move one is refused, while
highlights keep theirs (`vaultState.notesLocked`, `notesLock.js`), since
the file is then the one place a note is edited. One that pushes only PDFs
keeps them editable: a notes file is never read into Gamma there.

Elsewhere: the client's reads are `GET /api/sync/folders*`
(`routers/sync.py`, "The client"), and a vault on another server writes
through `POST /api/pages/{id}/plan` (`routers/collab.py`).

## The client

`gamma/gamma_sync.py` runs on its own: copy the one file to any computer
with Python 3.10 or newer and a token from Settings → Integrations (read
access is enough, unless PDFs are pushed: then a token that may write). It
pulls a folder into a directory and pushes the PDFs changed there, never
notes; it keeps its own state in the directory. The desktop app's server
takes its `Server`, `Push` and `settled`, which is why the file lives in
the gamma package; it imports nothing from it. `--help` on each command.

| Command | Does |
|---|---|
| `folders --server URL [--token T]` | lists the folders with their ids, and `root` |
| `init DEST --server URL --folder PATH\|ID\|root [--token T] [--save-token] [--no-notes] [--push]` | links the directory (made if missing): checks the token with `/sync/whoami`, resolves the folder by path (slashes between names, case and spaces ignored) or id, writes the state file; `--push` pushes the PDFs you change there at each `sync`, and needs a write token |
| `pull DEST [--watch SECONDS] [--full] [--force] [--dry-run] [--token T]` | Gamma's changes written, nothing pushed whatever the link says; with `--watch` again after every change in Gamma |
| `sync DEST [same options]` | push (with `init --push`), then pull; with `--watch` again after every change, found by polling the change feed from the manifest's cursor and, for a directory that pushes, by a stat of its PDFs |
| `status DEST` | what the directory is linked to and holds, and whether PDFs are pushed |
| `push FILE --page ID --server URL [--token T] [--watch SECONDS] [--dry-run]` | makes the PDF the page's file when its bytes are not the page's already: one call for a build script or a CI job, or with `--watch` each new version |

The token comes from `--token`, the `GAMMA_TOKEN` environment variable, or
the state file when `init --save-token` kept it there (then anyone with the
directory has it; a shared directory should not). `--watch` keeps the
process running: after a round it sleeps the interval, asks the change feed
whether anything moved past the cursor, and runs the next round only then,
or at once when a PDF moved in a directory that pushes; errors are printed
and retried at the next interval. Exit code 0 when the round ran (kept
files are reported, not errors), 1 on a refused token, an unreachable
server or a missing link. The console is told to replace characters it
cannot show, so a title in another script never stops a round on Windows.
An option's value that begins with `-` (one id in 64) is passed glued to its
option, the one spelling argparse takes as given.

`push` keeps no state file: the page is the state. It checks that the
token may write and that the page exists, waits until the file was last
written `SETTLE_S` ago (it runs the moment a build ends), and runs one
`Push` of the file in mode `file` ("A vault of one file"). It prints one
line: `sent` (with the reason when the highlights could not be moved), `up
to date`, `waiting: <why>`, or `not sent: <why>`. With `--watch` it looks at
the file every interval and runs again when it moved. Exit code 0 whether
or not anything was sent, 1 when the token is refused or may only read, the
page is not there, or the server cannot be reached at the start.

### What the client writes

```text
<dest>/
  .gamma-sync.json          the link: server, folder, and every file written
  Sub/                      a folder below the linked one; empty ones too
    A paper.pdf             the paper, the file Gamma stores
    A paper.md              its notes and highlights
  A note.md                 a page without a PDF
  A note/                   the pages inside A note (its sub-pages)
    Draft.md                one of them, its front matter naming A note
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
  the page's own filing order (`obsidian_export.Filing.folder`). Linking
  `root` takes the whole library: pages at the library root go to the top
  directory. The whole library is what the library lists: a work folder's
  file only once it is in the library, laid out by its library folders,
  and no work folder's directories ([work folders](work_folders.md)), so
  a repository is never copied this way.
- **A page inside a page** ([home_library.md](home_library.md#sub-pages))
  is filed where its top page is, its files in a directory named for its
  parent beside the parent's files (`<dir>/Parent/Child`,
  `Filing.directory`), as in the vault export.
- **One stem per page**, `<dir>/<Title>`, unique in its directory ignoring
  case (`obsidian_export.unique_name`), shared by the page's PDF and its
  notes file; the library pages are named first
  (`obsidian_export.name_files`). The PDF is written only when the server
  stores it; the notes file always, unless the link was made for papers
  only.
- **The notes file** is the vault page (`render_vault_page`) rendered
  against the whole folder: a mention of a sibling page is `[[Title]]`
  (`[[dir/Title]]` when two share a name), a linked block carries its
  `^anchor`, highlights are `[!quote]` callouts linking the PDF's page,
  labels are `tags`, the first front-matter line is `gamma_id: <page id>`,
  and a sub-page's next line is `parent: "[[Parent]]"`. Upload references
  are rewritten to `attachments/<name>` relative to the note's directory.
  Ink, sheets and text boxes are not in the file.

### The reads

Members read them, by a session or an integration token of either scope;
share links do not. All three are in `routers/sync.py`:

- `GET /api/sync/folders`: the folder tree as `{folders: [{id, path}]}`,
  for resolving a typed path (`--folder`) and for the desktop app's
  folder chooser.
- `GET /api/sync/folders/{id}`: the **manifest** (`folder_sync.manifest`),
  `{folder: {id, path}, cursor, dirs: [{id, path}], pages: [{id, title,
  stem, doc_id, pdf, pdf_size, notes, version}]}`. `dirs` holds the folders
  below and every page that has pages inside it, so the client makes that
  directory and removes it once empty. `pdf` and `notes` are the relative
  paths (`pdf` null when no file is stored), `doc_id` the PDF's identity,
  `version` the notes file's: the page's latest op seq and stamp, a digest
  of its labels' names, and for a sub-page of the text its `parent:` line
  names the parent by, so it changes whenever the file would. `cursor` is
  the change log's newest seq ([collab.md](collab.md#the-change-feed-gammarouterssyncpy)).
  `root` is the library; 404 for an id that is no folder.
- `GET /api/sync/folders/{id}/notes?pages=a,b,c`: the notes files of up to
  `MAX_NOTES` (200) pages, `{pages: {id: {markdown, attachments,
  version}}}`; pages outside the folder are left out.

The PDFs and pictures come from `GET /api/uploads/<name>`. Pushing a PDF
takes three more calls, each an editor's and so a write token's: `POST
/api/uploads`, `PUT /api/pages/{id}/attachment` with `keep_old_days`, and
`GET /api/blocks/{id}` for what the page shows now (its `doc`: the hash of
the document it owns and its name, `RemoteSink.current`). Before that last
read the client asks the change feed once (`GET /api/sync/changes?limit=1`,
`Server.check_format`): a server whose `format` is missing or below
`DOCUMENT_FORMAT` (52, the release where a page's document became one
property) keeps its pages' PDFs in properties this client does not read,
and the push is kept with "the server runs an older Gamma (workspace data
format N): update it to use this gamma_sync". An older gamma_sync against a
server of format 52 reads every page as showing no PDF; nothing on the
server can tell it, so **update gamma_sync together with the server**.

### Its round

`gamma_sync.Round` reads one folder through a source with three methods
(`manifest()`, `notes(ids)`, `download(name, target)`), `RemoteSource`
over HTTP. The state file `.gamma-sync.json` (`gamma_sync.Link`) holds the
link (`server`, `workspace`, `folder`, `folder_path`, `notes`, `push`), the
last round's `cursor` and `synced_at`, the `dirs` it made, and a `files`
map: per relative path the page, the identity (`doc` for a PDF, `version`
and `attachments` for a notes file, `attachment: true` for a picture) and
the `size` and `mtime` when written. A round only ever writes, renames or
removes paths it has in `files`, and a file whose size or time no longer
match counts as changed on disk.

With push on and a sink, a `sync` first pushes the PDFs changed on disk
(`Push`, mode folder: a page Gamma gave another file meanwhile is "changed
here and in Gamma", left to the pull, which keeps the disk's file;
`--force` takes Gamma's), then fetches the manifest and works through it:
renames (a page's file at a new path moved rather than fetched, when still
as written and nothing is in the way); directories; each wanted file
against the state (missing: fetched; same identity: unchanged; new
identity: fetched unless the file changed on disk, which is kept and
reported, as is a file in the way the round never wrote); the attachments
the notes name, once, and the ones no note names any more removed; the
files of pages that left the folder removed, a changed one kept and
forgotten; the directories the manifest no longer lists removed when
empty. Notes come in batches of 100, PDFs one by one into a `.part` beside
the target, moved into place once whole; a body shorter than announced is
an error, never a file. `--full` writes every file again (the way to pick
up a link text that lags), `--force` replaces the files changed on disk,
and `--dry-run` writes nothing.

## Limits

- **Markdown.** A note Gamma writes is patched, never rendered, but some
  edits cannot keep every law (the documented limits in "The lens"), and
  some are refused: a change that leaves a fence or `$$` open, or that would
  make another app read a block it left alone otherwise (`MEANING`). The
  person makes such a change in the file. A note past the importer's limits,
  nested past 200, or with a block past `ops.MAX_CONTENT`, is not edited.
- **What a file holds**: blocks with text and highlights. Ink, text boxes,
  pictures on PDF pages, sheets, recordings, chats, comments, reading
  positions and Gamma's trash stay in Gamma, as for the mirror.
- **One page, one vault**: a page filed below two vaults of this server is
  one's ("What lands on disk"), except that a vault that only pulls never
  takes one from a vault that pushes. Two vaults of this server that push
  do not overlap.
- **Over HTTP** no caret is seen, no relocation run starts, no overlap rule
  applies (another vault, here or on another computer, may push into the
  same folder), and a server older than this app cannot take a vault's changes.
  A PDF goes up as one request, so a proxy refuses a file past its body
  limit.
- **Only the desktop app** keeps vaults, and only while its server runs: a
  vault of a local server's workspace waits for that server to be opened,
  outside background mode, as clones do. A computer without the app runs the
  client, which pulls and pushes PDFs but never notes.
- **No file watcher**: a change on disk reaches Gamma at the next tick (30
  s), or within 2 s for a page open in a window, or when a window gets the
  focus.
- **Windows**: while Gamma serves a linked PDF it holds the file open, so a
  tool that replaces the file in that moment (`os.replace`, a LaTeX build)
  can fail with an access error and try again. A name Windows refuses, and
  two names differing only by case, are reported and left.
- **Cloud placeholders** are never read or removed: a file whose bytes are
  in the cloud waits until they are here.
- **Embedded annotations** of a PDF pushed are not imported; *Import* →
  *Annotations in this PDF* does that.
- **History** is kept but no route reads it yet: the bytes a write replaced
  are in vaults.db for 7 days.
- **The client's** link texts (`[[Title]]`) lag when the *other* page's title
  changes, until the linking page changes or a full round, and a new PDF
  dropped in its directory stays on disk.
- **A token** of another server expires as its issuer set it; a refused
  token is the vault's error until `PATCH {token}` replaces it.
