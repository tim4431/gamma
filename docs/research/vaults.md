# Synced vaults: your own directory opened in Gamma, and a PDF read where it is

Thought through in November 2026 from the code and from the two notes
before it, [folder-sync.md](folder-sync.md) and
[disk-to-gamma.md](disk-to-gamma.md). Three questions. Can Gamma work on a
local directory of Markdown and PDFs the way Obsidian works on a vault,
both ways, without ever damaging a file? Can it read any PDF and chat
about it without adding it to a library? And where does everything live
that a file cannot hold? Status: **built** (2026-11-07): steps 0 to 5
of "In steps", the reader, the vault of one file, the lens with its gate,
and today's two links taken onto the same engine. The mechanics are in
[dev/synced_vaults.md](../dev/synced_vaults.md), the desktop's half in
[architecture.md](../../desktop/docs/architecture.md) ("Synced vaults"),
and the user guide's
[Offline copies](../user_guide/user_guide.md#offline-copies) says it for
people; "As built" at the end lists where the build departs from this
note, and what is not built. The five decisions it first left open were
taken on 2026-11-06 ("Decided" below), after two surveys of other tools
and one of the code; the feature's name, its vocabulary and what becomes
of today's two links followed the same day ("One feature: the synced
vault"). An illustrated walk through the design, in Chinese, is kept
beside this note as [assets/vaults-zh.html](assets/vaults-zh.html); it
shows the plan, not the corrections.

## What exists, and where it stops

| Built | Does | Stops at |
|---|---|---|
| kept folder (then `dev/folder_sync.md`, now [dev/synced_vaults.md](../dev/synced_vaults.md)) | a library folder written to a directory: each PDF beside a notes file | notes files go one way; a new file in the directory is never taken in |
| followed file | one PDF on disk becomes a page's file on every change | the bytes are stored in Gamma; one file, one page |
| *Open with* ([desktop architecture](../../desktop/docs/architecture.md) "Opening PDF files") | a PDF handed to the app is uploaded and gets a page | always stored, on the server the window shows (`openOrigin()` in `desktop/main.js`), a NAS included |
| vault import and export ([dev/import_export.md](../dev/import_export.md#obsidian-vault-export)) | a zip that is an Obsidian vault, read and written | one shot each way |

What the code says about going further:

- **The notes file is a render, and the reader is an importer.**
  `obsidian_export.render_vault_page` flattens the outline (its doc calls
  re-import after a heading "the one lossy step"). `markdown_import.md_to_blocks`
  strips every line, drops the bullet character, turns tabs into four
  spaces, removes callout fold marks and cuts trailing space in fences.
  No block records its bullet or its indentation. Reading a file and
  writing it again does not give the file back. That is right for an
  import. A sync built on the pair would rewrite every file it touched.
- **Nothing maps Markdown onto an existing tree.** `md_to_blocks` makes a
  fresh one, paste-as-blocks inserts new blocks, the AI's `edit_block`
  rewrites one block's text. `sync_tree.diff` compares two trees, not
  text.
- **A kept folder is Gamma's.** Gamma picks the names (a title is a stem,
  a rename moves the file), writes `gamma_id` into the front matter and
  owns the layout. A person's existing vault is the opposite case: they
  own the names, the layout and every byte.
- **The state file is device state inside the directory.**
  `.gamma-sync.json` records a size and a time per file and changes on
  every round. In a directory that git or Dropbox also carries, that is
  noise and a source of conflicts.
- **The pieces a two-way sync needs are there.** A `set` with `base` is
  merged three ways (`textmerge.merge`). `ops.notify_commit` tells a
  listener that a page changed, and `page_changes` holds one row per page
  with its latest seq, so "the pages below this folder that moved since N"
  is one query. The links to disk have a store, a tick and a panel.
  Highlights are found again when a PDF changes
  ([dev/highlight_relocation.md](../dev/highlight_relocation.md)).

## What other tools do

Surveyed on 2026-11-06 from the tools' own documentation and source. The
links are under "Sources". What could not be checked is marked.

### What goes in the folder, and what stays with the app

| Tool | In the folder | With the app |
|---|---|---|
| Obsidian | `.obsidian/`: the vault's settings, and the open layout (`workspace.json`). Optionally `.trash/` | the registry of vaults (an id for an absolute path), the metadata cache (built again from the files), File recovery's snapshots (every 5 minutes, kept 7 days) |
| Syncthing | `.stfolder`, a marker; `.stignore`, never sent to other devices; `.stversions/` when versioning is on; temporary files `.syncthing.<name>.tmp` | the folder's ID, the configuration, the index database |
| Resilio Sync | `.sync/`: the folder's `ID`, the ignore list, the archive | |
| Nextcloud client | its journal, `.sync_<hash>.db`, in the folder's root, left out of its own sync | the account |
| rclone bisync | check files, when asked for | its listings |
| Unison | nothing | its archives |

- **A missing marker stops the folder.** Syncthing answers "folder marker
  missing (this indicates potential data loss…)" and syncs nothing until
  the marker is back; without that rule an unmounted disk would read as
  every file deleted. The marker is a directory with a file inside, so
  that cleanup tools leave it alone, and it is hidden.
- **Some add a guard against mass deletion.** rclone bisync refuses a run
  that would delete more than half the files (`--max-delete`); Unison asks
  before a whole replica goes (`confirmbigdel`). Syncthing has only the
  marker.
- **Nobody writes a file in place.** Syncthing writes a temporary file and
  moves it over the old one. It compares size and time before and after
  hashing, and a file that changed meanwhile ("file changed during
  hashing") waits for the next scan.
- **A watcher is not trusted alone.** Syncthing keeps a full rescan beside
  it because "the watcher can miss some changes".
- **Obsidian keeps no identity in the vault.** A vault is the path its
  registry names. Resilio is the one that writes an id into the folder.
- **Obsidian merges a file changed on disk into edits not yet saved**
  (since 0.11.6), and users report text dropped or doubled by it and ask
  for a way to turn it off.
- **Obsidian's deleted notes go to the system trash** by default; `.trash/`
  in the vault and deleting for good are the other two settings.
- **Obsidian advises against a vault inside a vault**: "Links may not be
  updated correctly".
- **A database in a synced folder is the known hazard.** Zotero: keeping
  its data directory in a cloud folder "is extremely likely to corrupt
  your database", because such services do not honour the file locks it
  relies on. Nextcloud's journal in the folder root is the counter-example,
  and works only because that client leaves it out of the sync.

### Where PDF annotations live

| Tool | Stored | Keyed by | Anchor | After a move | After new bytes |
|---|---|---|---|---|---|
| Obsidian, PDF++ | a link in a note (`#page=N&selection=…`); PDF++ writes into the PDF only when allowed | the vault path | text-layer indices | links are rewritten | fragile: 1.8.0 shifted every index by one |
| Obsidian Annotator | a JSON fence in the note | the note's `annotation-target` | Hypothesis's selectors | not checked | found again while the quote matches |
| Zotero | `itemAnnotations` in its database | the attachment's item id | page index and rectangles in PDF points | kept | misplaced when pages are reordered |
| Sioyek | `shared.db`; `local.db` maps a path to a hash | the content hash | coordinates | kept | lost |
| KOReader | a `.sdr` sidecar beside the book (the default), in one folder, or by hash | the path, or a partial MD5 | | by hash only | by hash, unlinked |
| Okular | its per-user data; into the PDF on *Save As* | the file's name and size (not rechecked) | | lost (the bug that led to saving into the file) | |
| Skim | extended attributes; a `.skim` file beside the PDF when asked | the file | | lost through mail and Dropbox | |
| Hypothesis | its server | the PDF's fingerprint: the trailer's `/ID`, else an MD5 of the first 1024 bytes | the quote with a prefix and suffix, a position, the page | kept | kept while the `/ID` is, which an incremental save keeps |
| Xournal++ | an `.xopp` naming the PDF's path | the path | page coordinates | the PDF is no longer found | |

- **Data keyed by a path and kept outside the file is what people lose on
  a move**: Okular's, Xournal++'s, Skim's. The tools that survive key on
  content or on an id of their own.
- **A content hash survives a move and not an edit.** The PDF's own `/ID`
  survives an edit by another reader. A quote with its context finds a
  passage again in either case, which Gamma's relocation already does.
- **The careful tools split in two**: where a file is belongs to the
  device (Sioyek's `local.db`, Zotero's path to a linked file), the
  annotations belong to a database that travels by the tool's own means
  (Sioyek's `shared.db`, Zotero's sync).
- **Zotero took its "Store Annotations in File" away** because it led to
  file conflicts and lost data, and keeps annotations in the database for
  "fast, conflict-free syncing". PDF++ "does not modify the PDF file
  itself" unless told to, and warns of corruption. Both offer an export
  into the PDF for other readers. Skim's maintainer warns that a `.skim`
  file edited on two machines conflicts.
- **Machine data in a person's Markdown is portable and breaks when
  edited** (Annotator's README warns about it). Indices into a text layer
  break when the renderer changes.
- **Where a PDF's notes go is a convention, not a rule.** Obsidian has
  none: any note that links the PDF is a note about it. Zotero Integration
  writes literature notes into one templated folder. Skim suggests a
  sidecar with the PDF's name so it is found without asking.

Not rechecked, from memory: Logseq, the nearest relative (an outliner with
block identity over Markdown files), rewrote files it had not written into
its own bullet form and has been moving its truth to a database; VS Code
keeps what a folder shares in `.vscode/` and what a machine knows about it
(workspace storage, Local History) in the user's profile.

## Decided (2026-11-06)

1. **A vault is a folder**, not a workspace: a folder of an ordinary
   workspace bound to a directory. The owner's choice, against this note's
   first recommendation. Several vaults are folders of one library. Asked
   next whether the kept folder and the followed file fit the same model:
   they do, and the vault is no third kind of link ("One feature: the
   synced vault" below).
2. **A directory vault writes `.gamma/` into the directory**, after
   Syncthing's marker: a hidden directory with a file inside, whose
   absence stops the vault. The file also carries an id, as Resilio's
   does.
3. **A PDF handed to the app is read where it is.** Adding it to the
   library is a second, asked-for step.
4. **A PDF's notes go beside it** under the same name, unless the vault's
   settings name a notes folder. Obsidian has no rule to follow here; a
   note elsewhere that names the PDF as its `source` is honoured.
5. **What Markdown cannot say stays in Gamma's database**: a highlight's
   rectangles, ink, text boxes. No sidecar. This is Zotero's and Sioyek's
   choice, and "PDFs and their annotations" below says why it fits.
6. **One feature, called the synced vault**, replaces *Keep on this
   computer…* and *Follow a file on this computer…*. Both become a synced
   vault with a direction set; neither keeps a name of its own.
7. **Git's words, in the interface too.** The Gamma server is always the
   remote; the directory or file on this computer is the local side.
   *Pull* brings Gamma's changes to the disk, *push* sends the disk's to
   Gamma. The clone settings already speak this way.
8. **`.gamma/` belongs to a directory, never to a file.** A vault of one
   file leaves nothing beside it; several such files in one directory
   leave nothing either, and the dialog offers the directory.

## The answer in one paragraph

Yes, on one condition: for a directory the person owns, the files are the
authority for everything a file can say, and Gamma writes a file only by
patching the bytes it last read, never by rendering the page again. The
feature is one, the **synced vault**: a link between something in Gamma
and something on this computer, with a scope (a folder and a directory,
or a page and one file) and a direction (pull, push, or both). *Keep on
this computer* was a vault that only pulls; *Follow a file* was a vault
of one file that only pushes; a person's own directory is a vault that
does both. A PDF opened from anywhere is read in place and becomes a
library page only when asked.

## One feature: the synced vault

`dev/folder_sync.md` (now [dev/synced_vaults.md](../dev/synced_vaults.md))
has two shapes of link, a folder with a directory and a page with a file,
and the folder link already has a direction: `push`, off or PDFs. The words are git's, with
the Gamma server as the remote and the directory as the local copy:
**pull** brings Gamma's changes to the disk, **push** sends the disk's
changes to Gamma, and the folder link's `push` column already says so.
There is no third kind, and no longer two: every link to this computer is
a synced vault, and a vault is a scope plus a direction. Syncthing gives a
folder the same three directions: Send Only, Receive Only, Send & Receive.

| Was called | Scope | Pull (Gamma to disk) | Push (disk to Gamma) | How it starts |
|---|---|---|---|---|
| *Keep on this computer* | a folder, a directory | on | off; or PDFs | a folder, an empty directory |
| a vault | a folder, a directory | on | everything: notes, PDFs, new files, renames, deletions | a directory, a new folder |
| a vault, read-only (step 2) | a folder, a directory | off | everything | a directory, a new folder |
| *Follow a file on this computer* | a page, one file | never | the PDF, stored in Gamma | a page's Document menu |
| an opened PDF (the reader) | a page, one file | never | the PDF, read in place | *Open with*; a vault row only once *Add to library* keeps following |

So a vault has two switches, pull on or off and push of nothing, PDFs or
everything, and the old names were settings of them. One side is empty
when a vault is made, and after that nothing tells the cases apart but
the direction. A vault of one file has one switch: its pull is never
offered, since Gamma never writes a PDF it did not put there.

**A vault of one file is the atom.** A directory vault's paper is a page
that follows its PDF, made by rule for every PDF below the directory. The
vault of one file made by hand stays for the two cases a rule does not
cover: a file in a directory that is not wanted as a vault (a build's
output), and a page on a server that cannot read this disk. Its page is
filed anywhere, and the files beside it are nobody's pages.

### The words

The interface uses the same few words everywhere, the ones the clone
settings already use (`SettingsMirrors.jsx`: *pulling*, *pushing*, *Force
pull*, *Force push*, *unpushed edits*). Git's other words have no meaning
here and are never shown: *clone* is taken by workspaces on another
server, and nothing is committed, branched, fetched or stashed.

| Word | Means | Shown |
|---|---|---|
| **synced vault**, vault | a link between Gamma and this computer, with a scope and a direction | the feature's name: the sync panel's section, the settings search, the user guide |
| **remote** | the Gamma server and workspace the vault belongs to; named by the server's name, never by the word alone | a vault's row: `D:\Papers ⇄ NAS · Research / Papers` |
| **local** | the directory or file on this computer | the same row, the dialog |
| **pull** | Gamma to disk: pages become files, renames move them, deletions trash them | the dialog's switch *Pull*, *Pull now*, *pulling*, *pulled 12 files* |
| **push** | disk to Gamma: files become pages, or a PDF a page's new file | the switch *Push: off / PDFs / everything*, *Push now*, *pushing*, *3 to push* |
| **sync** | pull, then push, whichever are on | *Sync now*, the 30 s tick's word, the CLI's `sync` |
| **up to date** | nothing to pull or push | the row's state, as a clone's |
| **conflict** | a block or a PDF changed on both sides since the last sync; the file won and Gamma's side is kept | *1 conflict*, the merge chip on the block |
| **untracked** | a file in the directory Gamma does not read: unknown kinds, the ignore list | *untracked: 4 files*, in the vault's status, never in the library |
| **force pull**, **force push** | replace the other side's changes with this side's, after a count and a confirmation | the row's menu, where the clone's *Force pull* and *Force push* are |
| **remove the vault** | the link goes; the files stay, or go to the trash, as asked; the pages stay | the row's menu, with the two choices |

Where they appear:

- **A folder's menu**: *Sync with this computer…*, in place of *Keep on
  this computer…*. The dialog names the directory, shows the two switches
  with pull on and push off for a folder that has pages, and says what
  the first round will do: *Pull 12 pages into D:\Papers; write `.gamma/`
  and nothing else.*
- **The launcher, the bar and the sync panel**: *Add a synced vault…*,
  in place of *Keep a folder…* and beside the planned *Open a folder from
  this computer…*, which is the same dialog started from a directory:
  push on, pull on, a new folder named after the directory.
- **A page's Document menu**: *Push from a file on this computer…*, in
  place of *Follow a file on this computer…*. The word says the direction
  the old name implied.
- **The sync panel's rows**: each vault with its scope, its remote, its
  direction as two small marks, its state, and *Sync now*, *Pull now*,
  *Push now*, *Pause*, *Force…*, *Remove the vault…*. The upload arrow
  that meant "send PDFs back" becomes the push switch.
- **A page of a vault**: the topbar says *Synced: D:\Papers\Attention.md*
  and, when the last write was refused, *not written to disk*, with the
  reason.
- **The CLI** (`gamma_sync.py`) already has `init`, `sync`, `status` and
  `push`; `pull` becomes the name of what `sync` does today, with `sync`
  kept for scripts.
- **The Chinese interface** uses git's own Chinese, 拉取 for pull, 推送
  for push, 冲突 for conflict, and Obsidian's own word for a vault, 仓库
  (its help says 打开其他仓库, 新建仓库), so a synced vault is 同步仓库,
  which is also what git's Chinese calls a repository.

### A vault of one file, and `.gamma/`

The marker answers two questions a directory raises and a file does not:
is this still the same directory (a mount point that is empty is not an
emptied vault), and may Gamma write here. A vault of one file raises
neither. Gamma never writes beside the file, and a file that is gone is a
page whose file is *missing*, never a deleted page. So:

- **One file: nothing on disk.** The link lives in Gamma's database, as
  Zotero's linked files do: the path, the hash, the size and time, the
  file id. The directory around it is not read, and the files beside it
  are nobody's pages.
- **Several files in one directory: still nothing on disk.** Each is its
  own vault, with its own page, filed wherever it is. The dialog that
  would add one more says *3 files in D:\Papers are synced one by one.
  Sync the folder instead?*, and that is the only hint.
- **A directory made a vault takes its files' vaults in.** Each page
  keeps its id, its notes and its annotations; it is filed in the folder
  that answers the file's directory, its old filings kept, and its own
  row goes. The reverse is refused by the overlap rule: a file inside a
  vault's directory is already a page, and the Document menu opens it.
- **A directory vault always has its marker**, whatever its direction.
  A vault that only pushes writes `.gamma/` and nothing else, after
  saying so, because the marker is what tells an unplugged disk from an
  emptied one, and that question is asked exactly of the side that pushes.
  Syncthing writes `.stfolder` into a Receive Only folder for the same
  reason.

### What changes for today's links

- **The renderer.** A link that can push needs files the parser reads
  back into the same tree, so the notes are written by the lens's encoder
  ("Notes both ways"), not by `render_vault_page`. Gamma's own files are
  written once more in that dialect; one changed on disk is kept, as now.
- **The state file.** `.gamma-sync.json` gives way to the marker in
  `.gamma/` and a base in the data directory ("Where everything lives").
  The gamma-sync client has no data directory and keeps its own.
- **`gamma_id`.** The base says which page a path is. The line is read
  where a file has it and no longer written.
- **One-way naming.** A title still names the file. With push on, the
  file's name also names the page.
- **The names.** The two menu items, the panel's words and the guide's
  two sections are rewritten in the vocabulary above; the rows of
  `folder_links` are the same rows, `kind` read as the scope.

What stays, on purpose:

- **Pull-only is a promise, not a stage to grow out of.** People put
  annotated copies in a pulled folder trusting that nothing goes back.
  What such a vault finds changed on disk is kept and reported, never
  pushed, and a force pull replaces it, as Syncthing's Send Only folder
  ignores the other side until *Override Changes*. Push is asked for, as
  pushing PDFs is now.
- **Pushing everything over HTTP comes later.** A NAS folder is pulled to
  a PC today by reads alone. Pushing notes and new files needs their ops
  sent with a write token ("In steps").
- **Stored or read in place is storage's question**, not the vault's: in
  place where the server is on this computer and found the file there,
  stored otherwise.

## Three layers

Each is useful alone, and each is built and tested before the next.

| Layer | What it is | Used by |
|---|---|---|
| L0 linked file | a page's PDF (or a note's picture) whose bytes stay at a path on this computer; Gamma reads, never writes | the reader, a vault of one file, a directory vault |
| L1 notes both ways | a page's block tree kept in step with one `.md`, by patches | a vault that pushes notes; later the notes files of one that only pulls |
| L2 directory binding | a root that is scanned: path is identity, directories are folders, renames and deletions are followed | a directory vault |

**L0.** A stored PDF is named by its hash, and everything downstream is
keyed by that name: the viewer's manifest and cache, the text index, the
AI's reading, a highlight's `pdf_position.doc_id`. So the change belongs
in `storage`: a named upload's bytes may also be at a recorded path
outside the data directory, with the size and time they had when hashed.
A read checks the stat first. A file that moved on is hashed again, and a
new hash is the replacement the followed file makes today, relocation run
included.

The seam is not whole yet. `storage`'s own docstring says every read goes
through it, and the upload route, `pdf_meta`, the text index, the AI's
`pdf_path`, relocation, translation and the folder sync do. But the
exports (`routers/export.py`), `ink_view`, `ops`, the mirror
(`sync_engine`), `zotero_local` and one import build
`uploads_dir / name` themselves. Those move onto the seam first; that is
worth doing whatever comes after. What lists the uploads directory never
sees a linked file, and that is the wanted meaning: no quota
(`storage.usage`), never purged (`upload_gc`), not in a backup
(`ws_backup`) or an off-site copy. A backup of a workspace with linked
pages holds their notes and annotations, not their files.

**L1** is "Notes both ways" below. **L2** is the next section.

## A vault is a folder

One rule, from which the rest follows:

> A page filed below the vault's folder is a file in the vault's
> directory, and a file there is a page filed below the folder.

It changes nothing about what a folder is. Filing stays as it is
everywhere in Gamma, many to many, checked only for shape
(`ops._filing_patch`); *Move to folder* still adds. The link decides
where a page's file is by the rule the kept folder already uses: the
page's first filing below the linked folder
([dev/synced_vaults.md](../dev/synced_vaults.md#what-lands-on-disk)). So
there is no second kind of folder, which [folder-sync.md](folder-sync.md)
turned down once, and no new refusal in any path that refiles a page.

From the disk:

- A directory is a folder. A `.md` is a page of notes, a `.pdf` a paper,
  filed in its directory's folder and titled by its file's name. The
  title is not an automatic one (`auto_title`), so a metadata lookup
  fills the paper's fields and never renames the file; *rename the file
  to the paper's title* can be a command.
- A file that changes changes its page. A file that moves changes the
  page's filing below the vault and leaves its other filings. A file that
  is gone moves its page to Recently deleted.
- Identity is the path. A path that vanished while another appeared is a
  move when the new file has the same file id or the same hash, or for a
  PDF the same `/ID` (moved and saved by another reader); a note moved and
  edited is told by similarity. Anything else is a deletion and a new
  page, and the deleted page waits thirty days with everything on it.

From Gamma, where the link learns of a change as the sync clocks do
(`ops.notify_commit`, then `page_changes` for the pages below the folder
and for the `folders` tree):

- A page made in the folder, or filed there, gets its files: its notes as
  `<dir>/<Title>.md`, and a PDF it had stored copied out beside them and
  followed from then on.
- A new title renames the file. A change of the first filing below the
  vault moves it. A folder renamed or moved inside the vault renames or
  moves its directory.
- A page moved to Recently deleted moves its files to the vault's trash,
  and a restore brings them back.
- A page that loses its last filing below the vault leaves it: a PDF read
  in place is stored first, then the files go to the trash, and the page
  stays a library page with everything on it. Deleting a folder in Gamma
  unfiles its pages as always, so they leave by this rule, and the
  directory goes once it is empty.

What has to differ, each in one place:

- **One page per document does not hold in a vault.** Two copies of a PDF
  in two directories are two files and so two pages. A page whose PDF is
  a linked file is left out of `blocks_store.page_for_doc`, and
  `replace_attachment` does not refuse it bytes another page carries
  (`HeldElsewhere`). The library keeps its rule: a PDF added there finds
  the library's page, never a vault's.
- **No push in a workspace that is a clone or has a Zotero link**, and a
  vault page is not published. The mirror pushes only files
  `storage.exists` finds in the uploads directory, so a clone would send
  the pages without their PDFs; a Zotero link would turn the folder into
  a collection. A pull-only link is allowed on both today, since it
  writes nothing into Gamma.
- **Vaults do not overlap.** A vault's directory is not inside another
  vault's directory or around one, and its folder is not below another
  vault's. A pulled vault on `root` or on a folder above leaves out the
  pages below a pushing vault (`folder_sync.layout` would otherwise render
  their notes into a second directory). A page filed below two vaults is
  the first one's; the second reports it and leaves it. A vault of one
  file inside a directory vault is taken in, as "A vault of one file"
  says.
- **`push` is read as on or off today**, by `run_link`, `due`, `tick`, the
  router and `desktop/lib/keeping.js`. Each learns its third value, and
  the switch that stops Gamma writing.

Making one: *Add a synced vault…* in the launcher, the bar and the sync
panel, started from a directory (*Open a folder from this computer…*).
The shell's picker names the directory. Gamma scans it and says what it
found and what it will write (the marker, nothing else) before it does
anything. It then makes a folder named after the directory in the open
workspace, when that is one of a local server and may have a vault, else
in the host's personal one. A linked directory is always a new, empty
folder: an existing folder and an existing directory are not merged.

Removing one: *Remove the vault…* asks what becomes of the files, kept or
moved to the trash, and never asks about the pages: they stay, with
everything on them. A PDF the vault read in place is stored first, so no
page is left pointing at a path Gamma no longer watches; the row says how
many and how much before it does it. Pages not wanted are deleted
afterwards as any pages are.

Several vaults are several folders of one library. There is nothing to
switch: tabs from each sit side by side, one search covers them (and can
be scoped to a folder), and a note in one mentions a paper in another or
in the library. In the file such a mention is the page's absolute Gamma
address, read back as a mention.

## Where everything lives

| Lives in | Holds | Why there |
|---|---|---|
| **the files** | note text and structure; `tags`, `aliases` and paper fields in front matter; links in the vault's own style; a highlight as a `[!quote]` callout that links the PDF's page, with a `^` anchor; the PDFs | anything else that opens the directory reads it: Obsidian, an agent, git |
| **`<vault>/.gamma/`** | `vault.json`: the vault's id, a format version, and the settings two computers must agree on (where a PDF's notes go, what to ignore). `trash/`: the files of deleted pages, for thirty days | the marker has to be where the files are; the trash on the same volume, so that a deletion is a rename |
| **Gamma's data directory** | the pages (trees with their block ids, the search index), each highlight's rectangles, ink, text boxes and pictures, chats, comments, tabs and reading positions; the link's base per file (text, hash, stat, file id); the bytes Gamma replaced, for some days; the PDF text index and covers | Gamma's own data, with Gamma's own merge and backup; or this device's knowledge |

- **The marker.** `.gamma/` is hidden and holds a file, as Syncthing's
  marker does, so cleanup tools leave it. A scan that finds no marker, or
  one with another id, stops the link and says so; it never reads that as
  deletions. The id is there because the path is not enough: a directory
  moved or renamed is found again by pointing the link at it, and Gamma
  can check that it is the same vault before it joins it to the pages it
  has. A second disk under the same drive letter is refused.
- **No `gamma_id` in a vault's files**, and no database in the vault.
  Nothing in `.gamma/` changes when a round runs.
- **Block ids are made on first read** and kept by the pages. A block
  gets a `^anchor` in the file only when something outside the file
  depends on it: a highlight, a link to that block. That is Obsidian's own
  habit and the vault export's rule.
- **The history is Gamma's.** What a write replaced is kept in the data
  directory, as Obsidian's File recovery and Syncthing's index are kept
  with the app. Nothing accumulates in the vault but the trash.

## Is Gamma a Markdown editor?

Asked on 2026-11-06, with Obsidian as the measure, and answered from the
code. Half yes.

**Inside a block, yes, and with Obsidian's own engine.** A block's
`content` is raw Markdown text and nothing else: no HTML, no rich-text
JSON (`markdown_import.py`'s docstring; the `unified_blocks` table in
`db.py`). The editor is CodeMirror 6, one instance in the block being
edited (`BlockCmEditor.jsx`), rendering math, references and marks live
and showing the source where the caret is, as Obsidian's live preview
does; the other blocks render through react-markdown with GFM, math,
callouts and KaTeX. Bold, italics, code, `==marks==`, links, `[[wiki
links]]`, images with a width, `$…$` and `$$…$$`, fences, Mermaid, tables,
tasks, quotes and `> [!callouts]` are typed as Markdown and stay Markdown
in the row. Enter continues a list, a task or a quote the Obsidian way
(`mdLines.js`), and `/` commands rewrite a line's prefix. None of this
needs to change.

**As a document, no.** Obsidian's note is one text, and its structure is
whatever the text says. Gamma's page is an outline: rows with a parent
and a position, every one drawn as a bullet (`BlockTree.jsx`), a heading
only a block whose text starts with `#`. A file reaches that shape through
an importer that decides where the text splits and nests, and the
decisions are lossy both ways, measured on 2026-11-06:

| In the file | In Gamma today | Comes back |
|---|---|---|
| what follows a heading | the heading's children | as children even when they were siblings (`test_obsidian_round_trip` says so) |
| a `-`, `*` or `+` list item | a block whose text keeps its marker (`- a`, `1. a`); `*` and `+` read as `-` | as written, with `-`, and a blank line after a top-level item that has no sub-items (a loose list) |
| two paragraphs inside one item | one block holding both | as one item |
| a list inside an item | the item's children | as a nested list |
| a blank line between paragraphs | a block boundary | as two blocks |
| `^anchor` | removed; the block gets a new id | only on blocks something links to |
| front matter | nine keys read, the rest dropped | nine keys |
| `%%comment%%` | removed by the vault import (kept as text by the plain Markdown import) | not at all |
| a callout's `+`/`-` fold | kept in the block's text | as written |
| a GFM table | a block, edited by cell | re-printed in a standard layout |
| an empty block | a row | not at all |

That table is the starting point of the round-trip gate below, and it
says what the lens has to carry outside the blocks: the dress of every
span (its marker, its indentation, the blank lines around it), the
anchors, the front matter's unknown keys, the comments. None of it needs
a new column on a block; it is the base's, per file.

**So a vault page is a Markdown file edited through an outline.** Once
the lens is built, every edit in Gamma is a byte-exact patch of the file,
and a file edited elsewhere arrives block by block. What a person used to
Obsidian notices: every paragraph has a bullet; a block nested under a
plain paragraph becomes a list in the file; what the parser does not know
(an HTML block, a setext heading, a footnote that spans paragraphs) is
one opaque block, shown as its text and editable as text, not rendered.

**A source view closes the rest**, and the lens makes it cheap: a vault
page gets *Edit the file*, a whole-file CodeMirror view of the base text,
as Obsidian's source mode and as `MdObject.jsx`'s *Edit markdown source*
already is for one object. Saving it writes the file through the same
path as a patch (parse-back check, compare with the file as it is, a
rename into place) and the page re-reads it as a change from disk. It is
the way to edit what the outline cannot show, and it is where a person
who wants Obsidian's feel for one file gets it. Proposed as part of step
3; a document view of the outline itself (no bullets, headings as
sections) is a presentational option left for later.

## Notes both ways: patch, never render

The mapping between a file and a tree has to obey the two laws of a
well-behaved lens:

- **Reading then writing changes nothing.** A file Gamma did not edit is
  never written, and an edit to one block changes that block's bytes only.
- **Writing then reading gives the same tree.** Otherwise Gamma's own
  write comes back as a change and the two sides chase each other.

A renderer gets the first law only when it is perfect on every file ever
written. A patch gets it by construction.

**Reading.** One parser turns a file into blocks and records each block's
*span*, the exact characters it came from; the spans and the gaps between
them tile the file. A block's content is its span without its *dress*: a
list item's indentation and marker, the indentation of its continuation
lines. A heading keeps its `#`s, as now. Hierarchy is as now: headings
hold what follows, items hold their sub-items, so a vault page looks like
any page. What the parser does not know (a setext heading, an HTML block,
indented code, a reference definition) is one opaque block whose content
is its span. Front matter is read for the keys Gamma knows and patched by
line, so unknown keys stay as written. Encoding, a byte-order mark, the
line ending and the last newline are the file's and are kept. A file that
is not UTF-8, or past the importer's limits, is a page that cannot be
edited, with the reason.

**Links** are the one place where the text in Gamma and in the file
differ: Gamma stores `[[page id]]` and an upload's address, the file says
`[[Title]]`, `[[dir/Title|alias]]`, `[text](path.md)` or
`![[fig.png|300]]`. The base remembers, per block, how each link was
written, and an edit that leaves a link alone writes it back that way.
Gamma's mentions have no display text, so an alias shows as the page's
title in Gamma and stays in the file. A new link takes the vault's habit
(Obsidian's settings when the vault has them: wikilinks or Markdown
links, shortest or relative paths, the attachment folder; the key names
are to be checked against a real vault). A renamed note breaks nothing in
Gamma, which links by id; the other files still say the old name, and
Gamma offers to rewrite them, counted, as Obsidian does.

**A change in Gamma** is written like this, a moment after its commit:

1. `sync_tree.diff(base tree, page tree)` gives the ops.
2. Each op becomes an edit of the base text. A `set` replaces the block's
   span with the new content in its old dress. An `insert` adds a span
   after the previous sibling's subtree, dressed like its nearest sibling,
   else like the file (its bullet character, its indent unit). A `move`
   cuts and pastes the subtree's span, indented for its new depth. A
   `delete` cuts the span and one gap.
3. The result is parsed and must give the page's tree. If it does not,
   nothing is written: the page says "not written to disk" with the
   reason, and the edit stays in Gamma. A parser or encoder mistake
   becomes a visible wait, never a damaged file.
4. The file must still be the base (size and time, then hash). If it is
   not, the disk's change is read first and the write starts over.
5. The text goes to a temporary file beside the target and is renamed
   over it (`storage.write_atomic`, with its Windows retry; the scan
   knows the temporary name and skips it). The bytes replaced go to the
   history. The new base records the text, hash and stat, so the scan
   knows its own write and nothing echoes.

**A change on disk** is read like this:

1. The file waits until it is settled, as a pushed PDF does. It is
   statted again after it is read, and one that changed meanwhile waits
   for the next scan. The same hash as the base only refreshes the stat.
2. The new blocks are matched to the base's: by anchor, then identical
   content in order, then the leftovers between matches pairwise as
   edits. The rest are inserts and deletes.
3. The target tree is the matched blocks plus the page's blocks that have
   no span (ink, sheets, text boxes), left where they were.
4. `sync_tree.diff(base, target)` goes through the normal op path as
   client `disk`, each `set` with its `base`. Text typed in Gamma since
   the base merges in, and an open editor sees the change arrive like a
   collaborator's.
5. The file and the resulting tree are the new base. If the page still
   differs from the file, the write above runs next.

**Both changed, same characters.** The file wins: the block takes the
disk's text, and Gamma's is kept as a conflict row with the existing
merge chip, and in the history. Obsidian's silent merge of the same case
is what its users report losing text to; here the merge is per block,
from a real base, and the side that lost is still there. No conflict copy
is written into the vault.

**What Markdown cannot hold.** Gamma is an outliner and a file is a
document. A block nested under a block that is no heading can only be a
list in Markdown, so that edit turns both into list items in the file. A
block holding a blank line at the top level reads back as two. Both
settle in one round, by Gamma taking the file's shape. Ink, sheets and
text boxes never reach the Markdown.

**The gate before any write is enabled.** The read side ships first with
a check that runs on every file of a vault and writes nothing: parse,
apply no edit, compare with the file. The number of files that do not
come back byte for byte must be zero on real vaults (an Obsidian vault,
a repository's `docs/`) before the write path is turned on, and the same
check stays in the tests as a corpus.

## PDFs and their annotations in a vault

A PDF found in the vault is a linked file (L0). A page that came from the
library with a stored PDF keeps it and gets a copy beside its notes,
which is then followed. Either way the PDF is only ever pushed, disk to
Gamma: Gamma never writes a PDF it did not put there, and a PDF replaced
in Gamma by hand stands until the file changes.

**The notes file.** A paper's page is its PDF and its notes. The notes
are the `.md` whose front matter names the PDF as `source` (what the
vault importer reads now), wherever it is; else the one with the PDF's
name beside it. It is made the first time a note or a highlight is
written, beside the PDF, or in the notes folder the vault's settings
name. Nothing is made for a PDF that is only read.

**A highlight** is, in the notes file, what the vault dialect already
writes: a `> [!quote]` callout whose title links the PDF's page, the
quoted text, the person's note under it, and a `^` anchor that says which
highlight it is. That is the part other tools read, and it is a quote
with a page, the anchor that survives anything. Its rectangles are not in
the file. Obsidian's `selection=` numbers are not written either: they
index one renderer's text layer and broke once already.

**Everything else stays in the database** (decision 5): rectangles, ink,
text boxes, pictures on the page. The reasons, from the survey and from
Gamma's own shape:

- Ink and text boxes need a merge that knows them. Gamma has one, by
  stroke and key by key, in its op path and its mirror. A sidecar in the
  vault would hand that data to Dropbox, Syncthing or git, which keep one
  writer's file and lose the other's. That is why Zotero took storing in
  the file away.
- A sidecar is written on every stroke, in a directory the person asked
  Gamma not to disturb.
- It would be a second truth beside the database, with a sync of its own.
- What users of other tools lose is data keyed by a path. Here it hangs
  on the page, which follows its file through a move by file id, hash and
  `/ID`, and through new bytes by the relocation run.

What that costs, and what covers it. A vault copied to another computer
brings its notes and each highlight's quote and page, not its ink or its
exact rectangles; the relocation engine places the highlights again from
their text, as rough matches to accept. Gamma's backups hold the pages
and so the annotations, and never the vault's files, which are the
person's to back up. The annotated-PDF export stays the way to hand
annotations to another reader, written when asked.

## Reading a PDF where it is

*Open with*, a drop on the window and *File → Open* stop meaning "add to
the library". Three cases come first: a file inside a vault opens that
vault's page, a file that is a vault of its own opens that page, and
bytes the open workspace already carries open their page, as now. Anything else
opens as a linked file on a local server, never on a remote one, so a
paper opened on a train is not sent to the NAS. It lands in the open
workspace when that is a local server's and no clone, else in the host's
personal one, and the bar says where.

Such a page is not in the library. The server lists every root page, and
"not listed" exists only in the client, for one case: a page inside a
page (`libraryPages` in `App.jsx`). So an opened PDF is a page inside one
page, *Opened files*, which is the only unlisted both sides already
share; search and the AI still find it, as they find any sub-page. The
alternative is a new marker that `root_pages`, search, the pickers and
the exports would each have to honour.

The page is a full one: highlights, ink, notes, chat. All of it lives in
Gamma's data, and nothing is written beside the file or into it. The
topbar says *On this computer* and offers **Add to library…**. In the
same workspace that stores the bytes, takes the page out of *Opened
files* and files it where asked. For another workspace or server it
stores the bytes there and carries the page across with its notes,
highlights and chat (the Gamma-to-Gamma export and import). A checkbox,
*keep pushing from the file*, makes the page a vault of one file, with a
row in the panel. *Opened files*
lists what was read, newest first. A file that is gone reads as missing
and can be located or removed. Pages with no mark and no chat are
forgotten after a while. A preference keeps the old behaviour for those
who want every opened PDF in the library.

Two things to know. The chat runs with the local server's AI settings, so
a person whose keys are on a NAS needs them here too. And comments for
others need the page on a server others reach, which is what *Add to
library* with *keep following* gives a manuscript.

## Rules that keep files safe

1. Making a directory vault writes `.gamma/` and nothing else, after
   saying so, whichever way it syncs. A vault of one file and the reader
   write nothing.
2. A file is written only because of something done in Gamma to that
   page: an edit, a rename, a filing, or a command that says what it will
   write (renaming links, counted). A note is written as a patch, checked
   by parsing it back, checked against the file as it is now, and put in
   place by a rename. What it replaced is kept for some days.
3. Gamma moves files to `.gamma/trash/` and never deletes one that has
   been there less than thirty days. A file deleted on disk moves its
   page to Recently deleted.
4. A file Gamma does not understand is left alone. Dot-directories,
   `node_modules` and the vault's ignore list are not read. A name
   Windows refuses, or two that differ only by case, is reported and not
   renamed.
5. A clash keeps the file and records Gamma's side.
6. **A root that is missing is offline, not empty.** No marker, or
   another vault's, stops the vault. A round that would remove a large
   share of the pages, or of the files, pauses and asks, as rclone and
   Unison do. A vault of one file whose file is gone is *missing*, and
   its page stays.
7. Paths are the picker's: a page of Gamma never names one, the shell's
   native dialog does, as today. Only the desktop app's own server binds
   vaults and reads linked files (the `GAMMA_FOLDER_LINKS` rule), nothing
   inside the data directory, no symlink followed out of the root.
8. The scan is the truth and a watcher is only a hint. A stat per file
   finds what changed (the push's loop already does this for PDFs): every
   couple of seconds while a page of the vault is open in a window, at
   once when the window gets focus, on the 30 s tick otherwise. A native
   watcher only brings the next scan forward, so a missed event costs
   seconds.

## What happens to what exists

- **Kept folders** keep their promise and their place: the way to put a
  library on a disk for agents and other apps, a NAS's included. They
  become synced vaults that pull, with push off or PDFs as their arrow
  was; the rows of `folder_links` are kept, `kind` read as the scope, and
  the first round after the upgrade writes `.gamma/` beside the files and
  says so in the panel. A vault of a local server can then have push
  turned on for everything, which is how an agent's edit to a notes file
  reaches Gamma.
- **Followed files** become vaults of one file, the atom: the way a
  manuscript's build reaches a page that others comment on. Nothing
  changes on disk for them.
- **The names** go: the two menu items, the panel's words, the guide's
  sections ("The words"). `gamma_sync.py` keeps its commands and gains
  `pull`.
- **Open with** becomes the reader.
- **A NAS.** A vault's round is written against the source and sink
  today's round has, so a folder of a NAS can push later: over HTTP with
  a write token, its PDFs stored there since a NAS cannot read this disk.
  That is the planned way to an iPad; a vault inside a clone that stores
  its PDFs would be another ("Still open").
- **LaTeX.** A project directory opened as a vault shows `build/main.pdf`
  as a page that follows every compile, highlights moved. It is also
  where [latex-projects.md](latex-projects.md) meets this note: a `.tex`
  file as a file-backed block is the trivial lens (content is the file),
  and on the desktop the project needs no materialised copy to compile.

## In steps

0. **The seam.** The readers that build upload paths themselves go
   through `storage`. No behaviour changes.
1. **The reader, and the words.** L0, *Opened files*, *Add to library*,
   the *Open with* change and its preference. The two menu items, the
   panel and the guide take the vocabulary of "The words", so that the
   rename lands once, before anything new is under it.
2. **A vault that only pushes.** The marker, the scan with its summary,
   pages from `.md` through the new parser, PDFs in place, changes on
   disk arriving live. Notes of a vault page cannot be edited in Gamma
   yet; highlights and chat work and stay in Gamma. The round-trip check
   runs on every file and reports.
3. **Notes both ways.** The patch writer, the history, conflicts; files
   made, renamed, moved and trashed from Gamma; highlights written as
   callouts; the link-rename offer; *Edit the file*. Gated on step 2's
   check.
4. **Today's vaults on the same rows.** The pulled vault's notes written
   by the encoder, its state out of the directory, and push offered on
   one: notes, and a PDF dropped there becoming a page
   ([disk-to-gamma.md](disk-to-gamma.md) step 3).
5. **Push of everything from a NAS folder**, over HTTP.

## Still open

- *Opened files* as a page with pages inside, or a marker of its own.
- The share of pages or files a round may remove before it asks.
- The trash in `.gamma/trash/`, or the system's, which the server cannot
  reach without a dependency.
- Pages inside pages in a vault: only where front matter says `parent:`,
  as the kept folder writes it.
- Files that are neither notes nor PDFs nor pictures a note shows: not
  listed at first.
- Whether mentions get display text, so an alias shows in Gamma too.
- Whether a vault in a clone stores its PDFs instead of reading them in
  place, so that the clone carries it to the NAS.

## Not proposed

- A database inside the vault, for the reason Zotero gives.
- Files as the truth for ordinary folders. They stay as they are;
  collaboration, clones and the iPad rest on that.
- Ids, front matter or reformatting added to a person's files on linking.
- Annotations written into a person's PDF, except by the export that says
  so.
- A file of annotations in the vault. If a vault ever has to carry its
  ink between computers with no Gamma server between them, the shape is
  known: one file per PDF, keyed by its hash, as Sioyek and KOReader key
  theirs.
- A vault inside a Zotero-linked workspace, or inside a clone while its
  PDFs are read in place ("Still open").

## As built

Built on 2026-11-07, in the order of "In steps"
([dev/synced_vaults.md](../dev/synced_vaults.md)). Where the code made the
design change, and why:

- **Bullets are content.** The survey above says the importer drops the
  bullet character and a callout's fold marks; it keeps both. A list
  item's marker is its block's text (`- a`, `1. a`), with `*` and `+` read
  as `-`, and a fold stays in the callout's first line. The round-trip
  table is corrected. The lens keeps the file's own bullet character, its
  spacing, box and indentation as the block's dress, so `* a` edited in
  Gamma is written back with `*`.
- **Sync is push, then pull**, not pull, then push as "The words" has it.
  Pull may write a file only while the file is its base, so the disk's
  changes are read first: a note changed since its base is pushed before
  pull patches it, and in a vault that does not push, it is kept and
  reported instead.
- **The file wins with a merge of its own.** The note sends each `set`
  with its `base` and lets the server's three-way merge decide. That merge
  works by characters, which reads a clash as clean (`10:16` changed to
  `10:56` and to `10:31` comes out `10:35`), and keeps Gamma's side when
  both changed one place. `vault_merge.file_wins` merges word by word
  before the write lock and is checked again under it. Where both sides
  changed the same words the file's text stands, and Gamma's is a
  `file_won` conflict written in the transaction of the ops, shown on the
  block's merge chip as *This file* against *Gamma*. The ops carry no
  `base`.
- **The target starts from the page.** "A change on disk" builds the target
  from the file's matched blocks plus the page's blocks with no span, which
  undoes whatever Gamma did since the base. The target is the page as it
  is, with only what the file changed since the base applied to it: a
  block the file did not touch stays where Gamma put it, a block Gamma
  added stays, and a block deleted in Gamma comes back only when the file
  changed its text.
- **A journal, not deterministic ids.** Block and page ids stay random,
  minted once. Before any effect a file's row holds what its base is about
  to become (`pending`, under a token); a push names its first batch with
  the token and its new blocks' ids begin with it, and the next round
  promotes the pending when the effect is found, or drops it and reads the
  file again. The worst a crash leaves is a block read twice, never one
  lost.
- **A second reading of the meaning.** The lens keeps one parser, Gamma's
  importer, so a page shows exactly what an import would make. But that
  parser and CommonMark read some text otherwise (a lazy continuation
  line, a setext heading, HTML left open, a table that takes in the next
  line), and a patch correct to Gamma's parser could change how Obsidian
  reads the block beside it. So before a changed text is written,
  markdown-it-py, a CommonMark parser in Python and the port of the parser
  behind VS Code's preview, reads the file and the new text, with tables
  and with `$$` as remark-math and Obsidian read it. A block the change
  left alone that would read otherwise refuses the write (`Unwritable`:
  "edit the file to make it"), and the page says *Not written to disk*. It
  is loaded only for that check, and costs about a second per megabyte of
  note.
- **The laws hold with named exceptions.** Where Markdown leaves no way
  for law 5 or 6 to hold (a heading moved parents what follows it, code by
  indentation that has to move becomes a fence, an empty box with no space
  after it), the file's meaning goes first, and the gate counts each case
  by name. The gate runs over the repository's Markdown, the Notion
  fixtures and an Obsidian-style corpus with no failure; a block held open
  in the middle of a file is refused, never written.
- **The note's open numbers, as set:** removals since the last quiet round
  past half of ten files or more hold the vault until *Go on*; a file
  counts as gone after two scans 10 s apart; the vault's trash keeps a file
  30 days at least; the history of what a write replaced lives 7 days in
  `vaults.db`, in the data directory and never in a workspace's backup; an
  opened PDF with no mark, comment or chat goes after 90 days untouched.
- **A vault that does not pull and pushes everything** locks its notes in
  Gamma (step 2's rule, kept): the notes are edited in the file, the
  highlights in Gamma. One that pushes only PDFs leaves them editable,
  since a notes file is never read into Gamma there.
- **Today's links** were taken onto the engine at their first round
  (`vault_upgrade`): the marker written, `.gamma-sync.json` read into
  `vaults.db` and removed, and each note still as the old client wrote it
  written once more by the lens's encoder, the old bytes to the history. The gamma-sync
  client keeps its own state file, gained `pull`, and pushes PDFs only.
- **Over HTTP** a vault of another server writes a page's plan in one
  request against the page's seq, and the text Gamma loses to the file
  goes to this computer's history, since the other server's conflict table
  cannot take it.

Not built:

- Pushing everything into another server's folder from the desktop app:
  the engine does it, and the dialog and the switch offer PDFs at most and
  keep Pull on.
- A native file watcher. The scan is polled: every 2 s for a page written
  in Gamma or open in a window, when a window of the app gets the focus,
  and on the 30 s tick.
- The offer to rewrite other files' links after a rename, *rename the file
  to the paper's title*, and a document view of the outline.
- A way to set the vault's settings in `.gamma/vault.json` (a notes folder,
  the ignore globs): they are read, and written empty when a vault is made.
- For a vault of another server, the refusals that look at its folder
  (two pushing vaults over one folder, a clone or a Zotero link of that
  workspace, a folder with pages and a directory with files), which only
  that server could answer; its directory is checked as any vault's. The
  refusal to mix a folder with pages and a directory with files on a
  change of push (it is asked when a vault is made). An upload in parts
  over HTTP, so a PDF past a proxy's limit is that file's problem.
- Everything under "Still open".

## Sources

Folders and sync:

- Obsidian help: [How Obsidian stores data](https://obsidian.md/help/Files+and+folders/How+Obsidian+stores+data),
  [Configuration folder](https://obsidian.md/help/Files+and+folders/Configuration+folder),
  [Manage notes](https://obsidian.md/help/Files+and+folders/Manage+notes),
  [File recovery](https://obsidian.md/help/plugins/file-recovery),
  [Internal links](https://obsidian.md/help/Linking+notes+and+files/Internal+links);
  the [0.11.6 changelog](https://obsidian.md/changelog/2021-03-09-desktop-v0.11.6/)
  and a [request to turn the merge off](https://forum.obsidian.md/t/editor-add-a-toggle-to-disable-automatic-merging-of-changes-non-obsidian-sync/14874).
- Syncthing: [FAQ](https://docs.syncthing.net/users/faq.html),
  [configuration](https://docs.syncthing.net/users/config.html),
  [syncing](https://docs.syncthing.net/users/syncing.html),
  [ignoring](https://docs.syncthing.net/users/ignoring.html),
  [folder types](https://docs.syncthing.net/users/foldertypes.html),
  [`folderconfiguration.go`](https://raw.githubusercontent.com/syncthing/syncthing/main/lib/config/folderconfiguration.go),
  [`blockqueue.go`](https://raw.githubusercontent.com/syncthing/syncthing/main/lib/scanner/blockqueue.go).
- [Resilio's `.sync` directory](https://docs.resilio.com/content/reference-information/what_is_.sync_directory/),
  [Nextcloud's client architecture](https://docs.nextcloud.com/desktop/3.9/architecture.html),
  [rclone bisync](https://rclone.org/bisync/),
  [Unison's manual](https://manpages.ubuntu.com/manpages/jammy/man1/unison-2.48.1.html).
- Zotero: [the data directory in a cloud folder](https://www.zotero.org/support/kb/data_directory_in_cloud_storage_folder).

PDF annotations:

- Obsidian: [1.3.6 changelog](https://obsidian.md/changelog/2023-06-26-desktop-v1.3.6/),
  [the 1.8.0 selection links](https://forum.obsidian.md/t/in-1-8-0-pdf-copy-link-to-selection-fails-to-copy-proper-links-in-some-cases/93545),
  [PDF++](https://github.com/RyotaUshio/obsidian-pdf-plus),
  [Annotator](https://raw.githubusercontent.com/elias-sundqvist/obsidian-annotator/master/README.md),
  [Zotero Integration's export settings](https://github.com/mgmeyers/obsidian-zotero-integration/blob/main/docs/Export%20Settings.md).
- Zotero: [annotations in the database](https://www.zotero.org/support/kb/annotations_in_database),
  [its schema](https://raw.githubusercontent.com/zotero/zotero/main/resource/schema/userdata.sql),
  [attaching files](https://www.zotero.org/support/attaching_files),
  [the PDF reader](https://www.zotero.org/support/pdf_reader).
- Sioyek: [usage](https://sioyek-documentation.readthedocs.io/en/latest/usage.html),
  [`database.cpp`](https://raw.githubusercontent.com/ahrm/sioyek/main/pdf_viewer/database.cpp).
- KOReader: [`docsettings.lua`](https://raw.githubusercontent.com/koreader/koreader/master/frontend/docsettings.lua),
  [issue 11773](https://github.com/koreader/koreader/issues/11773).
- Okular: [bug 151614](https://bugs.kde.org/show_bug.cgi?id=151614).
  Skim: [its manual](https://skim-app.sourceforge.io/manual/SkimHelp_37.html),
  [the maintainer on `.skim` files](https://mail-archive.com/skim-app-users@lists.sourceforge.net/msg05400.html).
- Hypothesis: [PDF fingerprinting](https://www.seanh.cc/2017/11/22/pdf-fingerprinting/),
  [`pdf.ts`](https://raw.githubusercontent.com/hypothesis/client/main/src/annotator/anchoring/pdf.ts).
  Xournal++: [its guide on PDFs](https://xournalpp.github.io/guide/pdfs).
