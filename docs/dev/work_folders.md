# Work folders

A *work folder* is a folder of the workspace kept by a synced vault of
this computer's server that pushes everything: a directory of working
files (a paper's LaTeX project, a repository) whose tracked files are each
a page, whatever their format: a note, a PDF, a LaTeX source, a figure, a
data file, a build (step 3, the class `file` of
[synced vaults](synced_vaults.md) "The classes"). Its pages are *files*.
They follow the disk: a file is filed in its directory's folder, named by
its file, and leaves when its file is deleted or untracked. They are not
library pages: the library lists none of them unless a file is added to it
by hand, and a PDF in a work folder is no paper (no record looked up, no
"one page per document") until it is. Every other folder vault (pull only,
PDFs only, a vault of the whole library, a vault of another server) is a
*kept folder* and behaves as before: notes and PDFs are its pages, and its
pictures are read for its notes' links alone.

The plan is [research/file-explorer.md](../research/file-explorer.md)
(step 1). Code: `gamma/work_folders/` (`library.py` the library rule,
`marks.py` the folder tree as the rules read it, `filing.py` the op rule,
`ignore_rules.py` the `.gammaignore` matcher and writer, `selection.py`
the tracking dialog's tree, preview and file, `takeover.py` the vaults
from before step 1), `gamma/folder_links.py` (which vaults are work
folders; making, saving, cancelling and removing one), `gamma/db.py`
(`BLOCK_HOT_COLUMNS`, `SQL_IN_LISTING`, `BLOCK_HOT_INDEXES`,
`folder_links.library`), migration step 53, and on the client
`frontend/src/documents/library.js` and `frontend/src/explorer/`. Tests:
`backend/tests/test_work_folders_library.py` and
`test_work_folders_filing.py` over the fixtures `tests/shared/library.json`
and `tests/shared/workfiling.json`, which `frontend/tests/library.test.mjs`
and `workFolders.test.mjs` read too; the op path's
(`test_work_folder_ops.py`), the readers' (`test_work_folder_reads.py`,
`test_library_routes.py`), the matcher's (`test_ignore_rules.py`, held to
git where git is on PATH), the round's (`test_work_folder_rounds.py`,
`test_work_folder_takeover.py`), the dialog's routes
(`test_track_routes.py`), the client's `knownPages.test.mjs` and
`trackSelection.test.mjs`, the scale check (`test_work_folder_scale.py`,
timing tests that run with `-m perf`) and the browser suite's
`work-folders` group (`frontend/tests/e2e/scenarios/workFolders.mjs`) and
its `viewers` group (`scenarios/viewers.mjs`: a work folder of eight
formats, each file opened in its pane, a region's comment reaching the
feedback document).

The words used here:

- **W** is a work folder's root folder (a folder block id).
- **A file** is a work-folder page: a root page whose
  `properties.work_folder` is set.
- **The mark** is `properties.vault` on W's folder block. **The stamps** are
  `work_folder`, `library` and `file_name` on a file's page row.
- **Writer**: the *mirror* (op client `sync`), the *vault* (op client
  `disk`), or a *person* (anything else: a browser tab, a share editor, the
  AI, metadata, Zotero, an import, a route).
- **W(f)**: the nearest folder on folder f's chain, f included, that
  carries the mark; none for a library folder and for an id that names no
  folder (`marks.Tree.work_of`).

## Which vaults are work folders

`folder_links.work_folder_link(link)` is the one test: a folder vault
(`kind` `folder`) of this server (no `remote_url`), of a folder (not
`root`), that pushes everything (`push` 2). It decides when the vault
writes the mark and the stamps, when it is taken over or let go, and what
removing it does. Readers and the op path never ask it: they read only the
mark and the stamps, which every server, clone, backup and client can
read. A round runs as a work folder's when the vault pushes everything and
its folder carries the mark (`Round.work`, the mark `remote.mark()` reads
from the folder's row, which `Layout.work` carries too): a vault of
another server that pushes everything into a folder marked there runs a
work folder's round, as the op rule there asks, while a vault that does
not push everything, or a round built on an unmarked folder, keeps a kept
folder's behaviour.

A vault that only pulls, or pulls and sends PDFs back, makes Gamma its
folder's source, so under the rule below nobody could rename or refile its
pages; a vault of the whole library has no folder to mark; a vault of
another server cannot use the tracking dialog yet. These stay kept
folders. A vault of the whole library is refused when it is made, or
turned, to push everything (`folder_links._whole_library`, from
`create_link` and from `set_push` when the push changes; the desktop shell
says the same, `keeping.WHOLE_RULE`); one that pushed everything before
step 1 goes on as a kept folder, since `check_vault`, which every round
runs, does not ask. Turning a kept folder's push to everything makes it a
work folder at its next round, its library switch on so that its pages
stay in the library.

## Stored

### The mark

```text
W's folder block: properties.vault = {"pull": bool, "library": bool}
```

- `pull` is the link's `pull` column. It tells the op path, on any server,
  whether the notes in W follow Gamma's renames and moves.
- `library` is the vault's library switch, `folder_links.library`. It says
  which `library` a new file's page gets.
- Only W carries it, never a folder below W. The key is `vault`, so the
  generated `work_folder` column is NULL on every folder row.
- Only the vault writes it (op client `disk`, on the `folders`
  pseudo-page): in the insert of a folder made for the vault, as a `set` on
  a folder a vault is made of, at the takeover, and from `set_pull` /
  `set_library` at once (one that cannot be written then waits for the
  round). `release` removes it (`vault: null`), when the vault is removed
  or its push is lowered, and a whole round writes it again where it
  differs from the row (after a restore or a mirror merge).
- The listing's folder tree carries folder properties, so the client sees
  the mark with the tree.

`marks.Tree` reads the folder tree (`tree_rows(conn, FOLDERS)`) into
`parent`, `names`, `marks` and `order`; `work_of(f)` is W(f), `mark(w)`,
`roots()` and `subtree(f)` the rest, `mark_of(props)` a folder's mark from
its properties. `filing_targets(tree)` are the folders in no work folder,
where a person may file a page. The op path reads the tree once a batch
(again after a person's move, insert or delete on the folder tree), and
once a bulk trash, when a rule needs it.

### The stamps

| Key | Value | Written by | Meaning |
|---|---|---|---|
| `work_folder` | W's id | vault, mirror | the page is a file of W |
| `library` | `true`, or absent | vault (from the switch, at the takeover), a person's new note (from the switch, `filing.creation`), Add and Remove (a person, through the op rule), mirror | the file is a library document too |
| `file_name` | the leaf name of the page's file (`main.pdf`, `notes.md`) | vault, mirror | the list view's Name column, and the path a later step builds from it |

- `library` exists only beside `work_folder`; clearing it deletes the key.
- `file_name` is the document's file's leaf for a page with a document
  (`doc.name`: the PDF's, or the file's of any other format), else the
  note's. It is written at create, by a move or rename on disk, by pull's
  rename of a note, and by pull's first write of a note made in Gamma; a
  note made in Gamma has none until then. It is stored because a library
  file's title is free and the vault's rows exist only on the desktop.
- `doc.size` is written for every document a work folder's vault reads (a
  new page, a new version, the takeover, a file vault taken in), listed
  ones included: the size the list view shows. Notes have none ("—").
- A file of another format than a note or a PDF has its document at `path`
  (`{format, name, hash, ext, at, size}`; its `ext` is
  `formats.vault_extension`, a blocked one `.bin`) or, past the keep limit
  (`GAMMA_VAULT_KEEP_MB`, 64), at `listed` with no hash: the page lists it
  without its bytes ([documents.md](documents.md) "The listed state"). Its
  title is the file's leaf, where a PDF's is its stem.
- A file never carries `parent_page`: no sub-pages in a work folder.
- `deleted_by_vault: true` is a trash stamp beside `deleted_at` /
  `deleted_by`, which `trash_page` writes for the op client `disk`: the
  page went to Recently deleted because its file was removed or untracked.
  A restore drops it with them.

### Columns and indexes

Two generated VIRTUAL columns on `unified_blocks`
([user_db.md](user_db.md) "pages.db"), derived from the properties, so the
step that adds them leaves the workspace format as it is:

- `work_folder`: `properties.work_folder` when it is non-empty text, else
  NULL (a folder row, a library page, and an empty or non-text value).
- `first_folder`: `properties.folders[0]`, a file's directory.

Three partial indexes:

- `idx_ub_listing` on `(parent_id, position)` where `parent_id = 'root'`
  and `SQL_IN_LISTING`: the library listing in its order. `parent_id`
  leads it, or the planner takes `idx_ub_parent` instead.
- `idx_ub_dir` on `(parent_id, first_folder)` where `parent_id = 'root'`
  and `work_folder IS NOT NULL`: a directory's files, paged, and the
  listing's `work_counts`.
- `idx_ub_work_folder` on `work_folder` where it is not NULL: a work
  folder's files, those in Recently deleted included (a stamp stays on a
  trashed page), for the takeover, `release` and the removal's counts.

SQLite uses a partial index only for a query that repeats its condition,
so the condition is one string: `db.SQL_IN_LISTING`, re-exported by
`work_folders.library` (`db.py` cannot import the package, which imports
it). `normalize.block_columns` drops every index whose statement names a
stale column anywhere, its condition included, and after the column makes
the hot ones again, and any other it dropped from its own statement.

### Migration step 53

`work_folders` ([migrations.md](migrations.md)). The global part adds
`folder_links.library` and turns it on for every vault that becomes a work
folder at its first round (a folder of this server, not `root`, that
pushes everything), so their pages stay in the library. The workspace part adds
the two columns and three indexes through `block_columns`. The step is in
`DERIVED_STEPS`: `SCHEMA_VERSION` is 53 and `workspace_format()` stays 52,
so mirrors and vaults of another server keep working across versions. No
stamp is written by the migration: which links exist only the desktop's
users.db knows, so the vault stamps its pages at its first round (the
takeover, below).

### How the pages that exist map

| Before | After the takeover |
|---|---|
| A page of a push-everything vault of this server's folder | a file of W, `library: true`, `file_name`; `folders = [its file's directory, …its folders outside every work folder]`; `parent_page` dropped; listed as before |
| A page of any other vault | unchanged: a library page |
| An Opened files page (read in place, a sub-page of *Opened files*) | unchanged: listed, and no library document (the default rule) |
| A file vault's page taken into a new directory vault | a file, `library: true` |
| Every other page | unchanged |
| A folder | unchanged; W gains the mark |

## The library rule

`work_folders/library.py`, over a page's properties; its client twin is
`frontend/src/documents/library.js`, and `tests/shared/library.json` holds
both, and the SQL, to the same answers.

```text
work_folder(P)      = P.work_folder if it is a non-empty string, else none
listed(P)           = work_folder(P) is none, or P.library is true
in_listing(P)       = listed(P), or P.pinned is a non-empty string
library_document(P) = P.library is true                  if work_folder(P)
                      not in_place(page_document(P))      otherwise
is_library_pdf(P)   = is_pdf(view) and own_hash(view) != "" and library_document(P)
follows(P, mark)    = work_folder(P), no doc, and mark.pull
```

- `listed` is what the library lists: its listing, the root feed, the
  label counts, the pickers' default lists.
- `in_listing` is what the listing route carries: listed pages and pinned
  files, which the client marks not listed, so a pin of a file survives
  without a read of its own.
- `library_document` is what is a paper: one page per document, the 409
  of an attach, the clipper's match, the record's lookup, the backup
  merge's match. An Opened files page is read in place outside any work
  folder, so it is none until added. The `at` test survives only as this
  default, for pages outside a work folder.
- A sub-page goes by its top for `listed`. For `library_document` it goes
  by itself: a sub-page is never a file.

The same in SQL, spliced into WHERE clauses over a `unified_blocks` row:
`SQL_LISTED`, `SQL_IN_LISTING`, `SQL_LIBRARY_DOC` and `SQL_LIBRARY_PDF`
(`SQL_PDF` and `SQL_LIBRARY_DOC`, with the `doc_id` column, which leaves
copies out). They read `library` as JSON `true` (`json_type`), as the
predicates read `is True`, so a stored `1` is no more a library flag on
one side than on the other.

`documents/model.py` keeps `SQL_PDF`, `SQL_IN_PLACE` and the document
predicates ([documents.md](documents.md)); which page is a library
document is asked only here.

## The op rule

`work_folders/filing.py` is the pure core: it reads a `marks.Tree` and a
page's properties and answers with what is stored, or `filing.Refused`
with the words a person reads. `ops.py` and blocks_store's page writers
call it; ops turns a refusal into a 403 with the conflict `locked`, never a
409 (the ops route retries a 409 for ever, and a refusal without a conflict
drops the whole collab batch). A tab that meets one drops that op alone
and says the refusal's words, in its language where they are the rule's
(`conflictKinds.refusalWords`, [collab.md](collab.md) "The work-folder
rule"); its title field and menus offer nothing the rule refuses, so only
a stale tab meets it.

Its client twin is `frontend/src/explorer/workFolders.js`:
`rootPatchAllowed`, `creationOf`, `mayTrash`, `mayRestore` and
`treeOpAllowed` answer a verdict, `{allowed: true, value}` or `{allowed:
false, kind, why}`, and `tests/shared/workfiling.json` holds both sides to
the same answers, case by case for each row below and each writer, the
kind of every refusal included. Over them, `fileRules(page, tree)` and
`folderRules(tree, folder)` say what the menus, the drops and the open
page offer, for a row of the page list or a block alike (`refile(folder)`:
the folders a drop leaves the page in, or the rule's words). What App's
menus, drops and open page ask of them about the pages in hand (a
refusal, what filing leaves out, a page menu's rows, a copy's
properties, the library requests and their words) is
`explorer/workFolderActions.js`, pure, and the commands over it, the
refusals with their status, Add to library, Remove from library and Save
a copy, and the menus' library rows, are `explorer/useWorkFolderActions.jsx`,
which App composes.

**Notes follow where the vault pulls.** A person may rename a file that
*follows* (a note, no document, in a W whose mark pulls), move it between
W's directories, trash it, restore it when the vault did not trash it, and
make one in a directory of W: pull renames, moves, trashes or writes its
`.md`. Everything else in W is the disk's: PDFs, pictures, a paper page (a
PDF with its notes), and the directories. A library file's title is free;
its file keeps its name.

The mirror passes every rule (it replays another copy's writes, and its
restore keeps the stamps it carries). The vault passes the person's rules
but is held to the shape, so a vault bug fails its file's round rather
than storing a bad shape.

**The shape (S)**, of what a write leaves on a page root, checked for the
keys the write touches (an unstamp at `release` leaves the folders as they
are, and they stop being W's once the mark goes):

- **S1.** A file of W is filed first in a folder of W
  (`W(folders[0]) == W`), then only in folders of no work folder, and those
  only while it is in the library.
- **S2.** Any other page is filed in no folder of a work folder: a library
  page is never filed in a work folder.
- **S3.** No `parent_page` on a file, and none that names a file.
- **S4.** `library` is true or absent and `file_name` a string, both only
  on a file; `work_folder` names a folder with the mark, or a folder this
  copy does not have (a clone that has the page before the tree).

**A person's root writes on a file P of W (R):**

| Key or action | A person | |
|---|---|---|
| R1 `work_folder`, `file_name` | refused | the vault's; a patch that repeats the value passes |
| R2 `doc` | refused | a file is read-only in Gamma; the vault's replacement (`LocalRemote.replace_doc` over `replace_attachment`) passes |
| R3 `folders[0]` | only when P follows and the new one is in W | pull moves the note |
| R4 `folders[1:]` | while P is in the library, library folders only | |
| R5 the title | while P is in the library (before or after the write), or follows | asked only when the title changes |
| R6 `library: true` | when P has no document, or its bytes are stored here, and no other library page owns the hash | the op path checks the bytes and `page_for_doc` ("Its bytes are on another computer.", "Already in the library as “…”.") |
| R6 `library: null` | allowed | drops `folders[1:]` in the same patch |
| R7 labels, `pinned`, `meta`, `auto_title`, other keys, blocks, comments | free | |
| R8 `parent_page` on P, or a target that is a file | refused | S3 |
| Trash | only when P follows | |
| Restore | only when P follows and not `deleted_by_vault` | |
| Purge from Recently deleted | free | |

A person's new page (`create_page`, `insert_page_tree`, `POST /api/pages`,
`POST /blocks` at the root, an import, a clip, `get_or_create_doc_page`)
goes through `filing.creation`. Its stamps are dropped (importers carry
old properties, so a refusal would break them). Folders that touch a W are
allowed only as a note at exactly `folders[0]` of a W that pulls; the
rule then stamps `work_folder` and, when the switch is on, `library: true`,
and keeps the other folders that are filing targets while it is in the
library. Any other filing in a work folder is refused (S2), and so is a
`parent_page` that names a file (S3).

**Folders by name.** A walk of folder names from the top
(`blocks_store.descend` with `works`, the W roots to pass by;
`folder_by_path` and `folder_inserts` with `into_work=False`, their
default) passes by every work folder and its directories, so an import, a
clip by path or the agent's write names a library folder, a new one beside
W when the names are W's. The vault walks into W (`into_work=True`), and
`folder_inserts` given another server's tree (`kids`, the HTTP vault)
passes nothing by. The agent's reading tools reach W's directories by
path.

**The folder tree, for a person (T)**, on the `folders` pseudo-page and
through `DELETE /folders/{id}` (`ops.tree_deletion`):

- **T1.** A folder below W is the vault's: no rename, no move to another
  parent, no delete, no insert below it. A reorder under the same parent
  passes, and so do props other than the mark (a pin).
- **T2.** W itself may be renamed (its name in Gamma; pull renames no
  directory for it) and moved under a folder in no work folder. It may not
  be deleted ("remove the vault") or have folders made in it.
- **T3.** No folder is moved under a folder of a work folder, and none
  whose subtree holds a W is deleted.
- **T4.** The mark is the vault's and the mirror's only.

**A restore of a stale file**, by the vault or a person: a page whose
`work_folder` names a folder with no mark (its vault removed while the
page was in Recently deleted) comes back as a library page, its stamps
dropped. A library page comes back filed in no folder of a work folder
(S2: a folder it was filed in became W, or one of its directories,
meanwhile), so in its library folders or at the top, and inside no file
(S3: its `parent_page` dropped when that names a file); a backup merge
puts no page inside a file either. The mirror's restore keeps what it
carries.

**The words**, `filing.refusal_text(kind)`, one table for the routes, the
AI tools and the client:

| Kind | |
|---|---|
| `filed` | A work folder's file is filed by its directory: add it to the library to file it in library folders as well. |
| `named` | A work folder's file is named by its file: rename it on disk. |
| `trash` | A work folder's file leaves when its file is deleted or untracked: use Choose files…. |
| `restore` | It comes back when its file is tracked again. |
| `directories` | A work folder's directories follow the disk. |
| `remove` | Remove the synced vault to delete its folder. |
| `document` | That file's PDF is read where it is: it cannot be changed in Gamma. |

S2, the folder tree's refusals and a person's new page filed in a work
folder answer `directories`, but deleting W, or a folder whose subtree
holds a W, answers `remove`; R1's `work_folder`, R3, R4, R8, S1, S3, a
`parent_page` on a new note and a `library` other than true or absent
answer `filed`; R1's `file_name`, R5 and a `file_name` that is no string
answer `named`; R2 answers `document`, a trash `trash` and a restore
`restore`.

**Who is the vault.** `auth.writer_client(request, claimed)` takes a
batch's `client`, else the `X-Gamma-Client` header on a route that takes
no batch (`POST /blocks` at the root, `DELETE /blocks/{id}`,
`POST /blocks/bulk`, `POST /api/pages` and the attachment routes, the
trash's restore, `DELETE /folders/{id}`), and honours the clients `disk`
and `sync` only on an integration-token request with no share; any other
request that claims them writes as a person. The HTTP vault names `disk`
on its API requests (`HttpRemote._asked`; a file's upload and download,
which the rule does not judge, go without it). A token that claims `disk`
is trusted as a vault: this
is an integrity rule, not a security boundary.

**Quiet stamps.** A root `set` of the vault or the mirror may carry
`quiet: true`: the page's `updated_at` stays, and a batch of nothing but
quiet sets still writes the page's row of the change log (so a mirror
sends the change) without stamping the page, so the takeover reorders
nobody's recents.

### What pull does with each change in Gamma

| A person in Gamma | Allowed | Pull |
|---|---|---|
| Renames a note that follows | yes | renames the `.md`, or patches `title:`, and sets `file_name` |
| Renames a library file with a document | yes | nothing on disk |
| Renames any other file | no | |
| Moves a note that follows to another directory of W | yes | moves the `.md` and sets `file_name` |
| Files a library file in a library folder | yes | nothing |
| Refiles a file any other way | no | |
| Trashes a note that follows | yes | moves the note to `.gamma/trash` |
| Restores it | yes, unless the vault trashed it | brings it back |
| Trashes or restores a PDF or a paper page | no | |
| Makes a page in a directory of a W that pulls, with no document | yes, stamped | writes `<dir>/<title>.md` where the selection tracks it, else reports it |
| Files a library page in W | no | |
| Renames, moves, deletes or makes a folder below W | no | |
| Renames W, moves W to a library folder | yes | nothing |
| Deletes W | no | |
| Labels, pins, notes, comments on any file | yes | labels patch `tags:` as before |

## What a work folder tracks: `.gammaignore`

What a vault tracks is a selection, and the selection is a file:
`.gammaignore` at the vault's root, read with git's ignore syntax
(`work_folders/ignore_rules.py`, in-house and stdlib only): comments,
escapes, trailing spaces, `!`, a trailing `/` for a directory, a `/` at the
start or in the middle anchoring, `*`, `?`, `[...]` with POSIX classes, the
four `**` forms, the last match winning, and nothing taken back from under
an excluded directory. It differs from git in one way: matching is always
case-insensitive and NFC, so one file reads the same on every system and
`*.pdf` takes `Paper.PDF`.

- **Root only.** A `.gammaignore` below the root is an ordinary file.
- **Never tracked**, before the rules and whatever they say: dot
  directories, `node_modules`, temporary names, links and junctions, names
  Windows refuses, case clashes (the dotted and dotless I read as `i`, as
  the matcher reads them), and `.gammaignore` itself. `!.git/`
  changes nothing. A link, a refused name and a case clash are reported
  in the round's status unless a line leaves them out first; a link is
  decided as a directory, so a line such as `/build/` leaves one out,
  unreported, and the default's `!*/` reports every one.
- **No file** is the default, `DEFAULT_TEXT`: notes, PDFs and pictures
  (every extension of those formats in `documents.formats`, `.jpeg` among
  them), the three classes a vault has always read. A taken-over vault
  has no file and tracks what it tracked, its pictures now pages (a work
  folder's picture is a file). Gamma writes a file into a
  directory unasked only once: a vault whose marker names an older app's
  `ignore` globs and that has no file gets them written at its first whole
  round (`Round._globs_written`: the header, the default, then each glob
  left out, a `/` at either end dropped as the old scan dropped it, one
  with a `/` inside anchored at the root, a leading `!` or `#` escaped,
  then, where this reading of the glob takes other entries of the
  directory than the old `fnmatch` did, case-sensitive on macOS and Linux,
  anchored lines that leave out or take back those entries), kept folders
  included.
- **A file that vanishes** after a round read one stops the vault ("its
  .gammaignore is gone: put it back, or choose its files again") rather
  than falling back to the default; one that cannot be read (a link or a
  junction, a directory, more than 256 KB, not UTF-8) stops it too, the
  dialog's routes answer it 409 and a vault's preview gives it as its
  refusal. A walk outside a round (`selection.rules_of`: `create_link`'s
  test of a folder and a directory that would mix, opening a file with
  the app) reads the default instead, the round saying what is wrong. The
  link's `state["selection"]` holds the hash of the text the last whole
  round read. A hand edit is read like a selection saved in the dialog,
  its removals held by the guard.
- **`.gitignore` is a hint**, not a filter: the vault's `.gitignore` files,
  those of its ancestors up to the repository's top and
  `.git/info/exclude`, read with the same matcher, mark rows "git ignores
  it" in the dialog.
- **The cap**: a work folder tracks at most `selection.MAX_FILES` (20,000)
  files. The dialog's Save refuses more, and a round that selects more than
  `state["cap"]` stops before writing. The takeover sets the cap to at
  least the files a vault already tracks, so no vault is stopped by the
  upgrade.

The writer (`ignore_rules.write`) writes the shortest form that says a
selection: per directory, anchored lines only, either the unticked files
and wholly unticked subdirectories, or `/D/*` and the ticked ones taken
back, whichever is shorter; a folder ticked whole gets no line, so files
made in it later are tracked; extension groups (`/D/*.pdf`, `!/D/*.pdf`)
for two files or more of an extension when every child file of it is an
exception. The groups come in name order and the names NFC, so one
selection writes the same text, and the same hash, on every system. It
reads its output back over the walk and falls back to the exact baseline
form if they differ, and refuses (`Inexact`, the dialog's 400) a selection
that form does not say either, rather than write a file that leaves out
what was ticked.

The matcher indexes its lines rather than compiling one regex of them all,
whose cost grows with the square of its groups: a line with no wildcard by
its path (a name pattern by its name), any other by the directory its
literal part names, in regexes of at most 64 lines. A `.gammaignore` of
thousands of lines costs a fraction of a millisecond a decision. Against
git, besides the case: under `core.ignoreCase` git's set of one uppercase
letter (`[A]x`) matches neither `Ax` nor `ax`, where this one matches both,
and git's `?` and sets match a byte where this one matches a character.

## The tracking dialog

**Making a work folder.** The desktop shell picks the directory, since only
the shell may name a path: *Add a synced vault…* makes one in a new folder,
and *Sync with this computer…* on an empty folder of a local server whose
directory holds notes or PDFs makes that folder one (`makeWorkFolder`). It
posts `POST /api/folder-links` with `push: "everything"` and
`choose: true`, and opens the page with `?track=<link id>`
([desktop architecture](../../desktop/docs/architecture.md)). The server
makes the folder with its mark (or marks the folder it was made of) and
the marker, and keeps the link paused and *choosing* (`state.choosing`,
and `state.made = {folder, marker, marker_dir}`, what making it made, for
a Cancel): every way into a round refuses it ("choose its files first"),
a resume too, and the file vaults inside wait for the Save. Every other
vault (of a folder with pages, or with a directory of no notes or PDFs,
of another server, of one file, of the whole library) still asks in the
bar, as before.

**The routes**, by the link's id, never a path, for the link's maker
alone (`routers/folder_links.py` `_chooser`: another editor gets 403,
anyone else 404, and so do a file vault and a vault of another server;
[api.md](api.md)). `work_folders/selection.py` holds the tree, the
evaluation of a selection, the preview, the save's checks and the file's
writing; `folder_links.save_selection` holds the save's lock, the link's
state and its round.

- `GET /api/folder-links/{id}/tree?dir=&base=file|preset`: the directory
  listed whole, breadth first, its entries with their format, size, time,
  whether git ignores them, whether
  they are selected under the base (a directory's own value: the preset's
  "not git-ignored", the file's whether it tracks the directory), their
  page and its other file, and why one is never tracked (a dot directory,
  `node_modules`, a link, a name Windows refuses, a case clash, a name
  that is no UTF-8); `.gammaignore` and temporary names are no rows. The
  outermost directory git ignores, and any directory the budget (50,000
  entries, 3 s) did not reach, comes back unwalked, and is read with `dir`
  when it is opened (below a git-ignored one, all of it). It writes
  nothing.
- `POST …/selection/preview {selection}`: what a selection comes to: files,
  bytes, by format, how many are past the keep limit (`large`, with
  `keep_bytes`: files of other formats than notes and PDFs the vault lists
  without their bytes), the cap, the pages that
  leave (with their notes and open comments), the files joined to their
  page's other file, the `.gammaignore` a Save would write, and
  `rules_ours`, true when that replaces nothing a person wrote (no file
  there, or Gamma's own), so the dialog warns only before it rewrites a
  hand-written one. Every file it tracks is a page (the tree's `kind`:
  `notes`, `pdf` or `file`).
- `PUT …/selection {selection, base_hash, library?}`: refused (409, with
  the hash there now) when the file changed on disk since it was read,
  checked again under the link's lock; else takes in the file vaults
  inside, keeps a hand-written old file in `.gamma/trash/`, writes the new
  file (always, a selection equal to the default too), sets the library
  switch when given, unpauses whatever paused the vault and runs a
  confirmed round (saving is the confirmation the guard asks for, the
  dialog having said what leaves).
- `DELETE /api/folder-links/{id}` on a choosing link cancels it: the link
  goes, the marker when making it wrote one (an adopted marker stays),
  `.gamma/` when it made that and it is left empty, the folder when it was
  made for the vault and nothing is filed there. A folder it was made of
  loses its mark, and a note made in it meanwhile stays as a library page.
  No `.gammaignore` was written.

**The preset** of a new work folder ticks PDFs and notes unless they lie
inside a directory git ignores (`build/`, `out/`, `venv/`). A PDF ignored
by a file pattern alone (`*.pdf`, `main.pdf`), as a paper's built PDF
usually is, is ticked, and its row says "git ignores it". The format
filters (PDFs, Notes, LaTeX sources, Pictures) tick by format in one click.
A paper's PDF and its notes are tracked together: the dialog joins pairs.
The PDF of a vault of one file inside is always ticked (`locked`), since
the save takes that vault in.

**The web dialog** is `frontend/src/explorer/TrackDialog.jsx` on the import
review's tree, opened from `?track=` (the shell's hand-over, read once and
taken off the address), from *Choose files…* on W's folder menu, on a
file's vault card and in the sync panel (the desktop, a link of this
server), and from an empty work folder's line. It reads the directory
whole, as far as the tree's budget goes, and a folder that came back
unwalked when it is opened. A work folder still choosing starts from the
preset, any other from its file. The choice is a base and the changes
made on it in order (`explorer/trackSelection.js`: a file, a directory
whole as `dir/`, every file of a format; Select all and Deselect all
write one change per top-level entry, since no change names the whole
directory), so a folder not read yet is ticked or unticked whole (one its
base tracks shows part ticked until a change covers it: nobody has read
what the base leaves out of it). Its summary line is the preview, asked
400 ms after the last change, and Save waits for the preview of the
choice as it stands, since saving confirms what leaves; the files the
preview joins to their page's other file show ticked, not to be unticked
alone, and the summary lists them, and says how many chosen files are over
the keep limit and are listed without their bytes. It carries the library
switch (off to start for a new work folder, else the link's; it adds notes
and PDFs, never a file of another format); Save writes the selection and
the folder's list is read again once the round is over; Cancel on a new
work folder asks first, then removes it.

## The folder's list view

Opening W or one of its directories shows `explorer/FolderList.jsx` in
place of the library's grid or list ([home library](home_library.md)
"Work folders", [UI design](ui-design.md#lists-that-change)): the
directories first, by name, each counting the files below it (the
listing's `work_counts`, summed down its subtree), then the files from
`GET /api/folders/{id}/pages`, sorted on the server, 200 rows at a time
(`explorer/useFolderRows.js`, read again whenever the listing is). The
columns are Name (`file_name`, else the title of a note not written yet),
Format (`libraryUtils.pageFormat`: its document's, else its name's, a
text file by its language; `formatLabel` names a format or such a
`{format, language}`), File size (`doc.size`; a note has none),
Modified and Comments (its open threads). The sort is the list's own
(name, format, size or modified), kept in this browser. The list draws
its rows itself; what a row does (select, open, its menu, its pin, a
rename, a drag) is the library's, which App hands in.

## Pages the listing does not carry

The listing leaves out the files not in the library, so the client reads
them where it shows them: the folder's rows, and the summaries
(`POST /api/pages/summaries`, `explorer/useSummaries.js`) of the files
open in a tab, viewed lately or open now, each read again with the
listing. App merges them with the listing's rows into `knownBlocks`
(`explorer/knownPages.js` `mergeKnown`, the listing's row winning), which
the page list, the tabs' icons, the chat and the citation pills read. A
row's `_listed` (by its top; a top the client does not know counts as
listed) keeps the library's own lists, the label counts and the pickers'
default lists to listed pages, the tabs' and the recents' aside
(`pickerPages`), so a file stays in Recently viewed, Open tabs and the
Pinned strip, and the chat's pickers offer it.

## The round in a work folder

([synced vaults](synced_vaults.md) holds the round itself, and "The
classes" the class `file`.)

- **Every tracked file is a page**: a note (`notes`), a PDF (`pdf`), or a
  file of any other format, pictures included (`file`: its document the
  file, read where it is, or listed without its bytes past the keep limit,
  `GAMMA_VAULT_KEEP_MB`). A picture is no longer only what a note links:
  the default selection makes every picture a page, and a note's
  `![[fig.png]]` keeps its Gamma text `![](/api/uploads/<hash>.png)`, which
  the picture's page's row serves. A PDF by its bytes alone, under another
  name, is a binary file; a `.bat` is stored as `.bin`.
- **New pages** are stamped: `work_folder`, `file_name`, `library` from
  the switch for a note or a PDF (never a file of another format),
  `doc.size` on every document. A file of another format is titled by its
  leaf (`main.tex`), a PDF by its stem. The front matter's `parent:` makes
  no sub-page.
- **A move or rename on disk** refiles `folders[0]` to the new directory,
  keeping the folders outside every work folder, and sets `file_name`; the
  title follows the stem only for a file not in the library whose note has
  no `title:`, and the leaf for a file of another format. A new name that
  says another format or extension (`a.txt` to `a.png`) settles the
  document again with the same hash; one that changes the class (`a.txt`
  to `a.md`) is a removal and a new page.
- **A change** of a file read in place replaces its document (its
  versions stored while it is in the library or has an open thread); a
  touch with the same bytes keeps it served. A file that crosses the keep
  limit either way, or is listed with a new size, has its document set
  anew.
- **Pull** moves or renames only notes that follow, never a PDF or a file
  of another format, and writes no note beside such a file (its notes are
  Gamma's own); writes a
  note made in Gamma only where the selection tracks its path (else it
  reports it and tries again next round), and writes or takes no other
  path the rules leave out (a picture a note links, a file back from the
  trash, an older note it would adopt as a page's); never copies a PDF
  into the directory; writes a picture a note made in Gamma links where
  tracked, which the next whole round makes a page; buries the row in
  place when a page is trashed, for
  its PDF, a paper's notes, a file of another format and a note the rules
  leave out, so that file is
  never moved; renames and moves no directory (a folder below W renamed in
  Gamma by a mirror of an older copy, or a restore, leaves the disk as it
  is).
- **A page back with its file** (a file at the place of a tombstone, a
  directory gone and back) is filed in that file's directory again, its
  folder made where missing, then in its folders outside every work folder.
- **A change of selection.** A file the selection leaves out now, while it
  is still on disk, is *deselected*: its page goes to Recently deleted
  (`deleted_by_vault`) with an *in-place tombstone* (the row's path kept
  as `\x00kept/<path>`, `was` the path, no pending; one a path, a newer
  one taking an older one's place), and comes back with everything on it
  when the file is tracked again, whatever its bytes. The file is never
  moved. The guard counts deselected pages beside removals. One of a
  page's two files left out by a hand edit is forgotten, the page keeping
  the other, and so is the other of a page deselected whole when one of
  its files is ticked again (the dialog joins the two: its pages count the
  in-place tombstones). In a kept folder a deselected row is forgotten and
  its page stays, and so is an in-place tombstone a work folder left
  before its push was lowered.
- **A purge.** Once a page is purged from Recently deleted its in-place
  tombstone is forgotten, its PDF stored first for any other page that
  reads those bytes in place (`spare`). A page purged while its file is
  still on disk and tracked is made again from the file at the next round,
  as any new file is.
- **A library file's versions** are stored as they come, as a library
  document's bytes are; so are a file's while a comment thread on it is
  open (`feedback.kept.keeps_bytes`, the one predicate at the replacement:
  the version a thread was opened on is held until it is resolved,
  [feedback.md](feedback.md) "The commented version kept"); its document
  still reads in place here.
- **The feedback files.** Every whole round writes the work folder's
  `.gamma/feedback/` after its sync (`open.json`, `open.md`, the pictures,
  a file per Send; the summary in `status.feedback`), and the hot loop
  writes them within a pass of a comment, a reply, a resolve, a relocation
  or a Send ([feedback.md](feedback.md) "The files"); a paused or choosing
  vault writes none.
- **Opening a file with the app** (`linked_files.open_pdf`) inside a work
  folder that does not track it, or one still choosing, is refused: it
  opens from the work folder once ticked in Choose files….

## The takeover

`work_folders/takeover.py`. At a work-folder link's first round without
`state["work_folder_at"]` (`TAKEN_OVER`), before the round,
`take_over(link, remote)` writes the mark, then stamps each page of the
vault's layout (not the pages it shares with another vault), quietly, a
chunk of pages a transaction: `work_folder`, `library: true`, `file_name`
from its rows, `folders` with the directory of its row's path first (its
folder made where missing; a note with no row, which pull writes, keeps its
folder in W, else W) and its folders outside every work folder after,
`parent_page` dropped (sub-pages flattened), `doc.size`. A page with no
file on disk that would not follow (a PDF stored in Gamma, any page where
the vault does not pull), and the sub-pages below it with none either, is
no file, which no round would trash and no person could: it stays a library
page, filed only in its folders outside every work folder. The cap is the
larger of `MAX_FILES` and what a scan under the vault's rules counts, as a
work folder's round counts it (every file the rules take, pictures
included). It is idempotent per page, so a killed
takeover resumes at the next round; the rows' `seq` moves with the page
where it was level, so pull rewrites no note, and a row's recorded folder
is corrected to its directory's. The link's state with `work_folder_at`
is the commit point, and its status shows when it became a work folder. A
work folder made in a new folder, or of a folder that holds no page, gets
`work_folder_at` when it is made; one made of a folder with pages (the API
allows it while the directory holds no file), a kept folder whose push is
turned to everything, and every vault from before step 1 are taken over
at their first round as one. A vault of one file that a new
directory vault takes in (`folder_links._take_in_one`) has its page
stamped the same way, in the library.

`release(link, remote, *, trash_files)` unstamps every live page of W
(quietly), sends the files not in the library to Recently deleted instead
when `trash_files`, forgets the vault's rows of files of other formats
(their pages keep their documents; a kept folder's round compares no such
class), then removes the mark. A link that stops being a work folder (its
push lowered) is released at its next round, its files library pages.
Lowering the push is refused while a file is not in the library.

## Removing a work folder

Removing the vault stores every PDF read in place (a trashed page's too, so
a restore has its bytes) and nothing for a file of another format: Gamma
copies nothing of a directory but what was asked to be kept, a library
file's versions and a commented version, which are stored already, and
every live page whose bytes are stored shows the stored copy from then on
(its document at `store`, whatever its format); such a file's page in
Recently deleted reads its file as missing. It releases W
with `trash_files`: library files
stay as library pages and every other file goes to Recently deleted. Then
the directory folders below W that hold no live page are deleted, deepest
first, and W with them when it is empty. A link that was a work folder and
has not been let go yet is let go the same way. The feedback files
(`.gamma/feedback/`) are deleted with the marker, whatever becomes of the
other files, and when a work folder is let go at its round because its
push came down: they are Gamma's own and regenerable, never trashed
([feedback.md](feedback.md) "The files"). `removal(link)` says both
numbers: the files that go to Recently deleted and those that stay in the
library.

## Add to library, Save a copy, Remove

Pages routes, so a NAS serves them too ([api.md](api.md)):

- `POST /api/pages/{id}/library {folder?}`: a file not in the library
  becomes a library document, one page still at its path, whatever its
  format (a PDF, a figure, a data file): its bytes are stored under the
  document's extension (from this computer's path when they are not),
  `library: true`, for a PDF `auto_title` set to its title as it stands so
  its record may title it, filed in a library folder when given, a PDF's
  manifest scheduled (`pdf_meta.schedule`; the record's lookup stays the
  client's, as on any library PDF). Bytes another library page owns answer
  409 with that page; bytes no path here holds, or a file changed since,
  answer 409 too, and so does a listed file (409 `TOO_LARGE`, "This file is
  too large to keep in Gamma."): past the keep limit there are no bytes to
  store.
- `DELETE /api/pages/{id}/library`: back to a file alone, its library
  folders and `auto_title` dropped and its title reset to the file's name
  as the vault titles it: a PDF's stem, any other file's whole leaf
  (`fig.png`).
- `POST /api/pages/{id}/library-copy {folder?}`: *Save a copy to library*,
  for a file with a document (every format but a note, whose content is
  its blocks): a new library page from the bytes as they are now
  (`get_or_create_doc_page`, titled by the file's name), annotated apart, a
  figure's version kept apart; a library page that owns those bytes already
  answers instead (`existed`, which the client says, offering that page,
  rather than a copy made); a listed file is the same 409 `TOO_LARGE`. The
  menus offer it only for a file not in the library, whose own page the
  copy would otherwise be.

## The readers, classified

Every reader of root pages, by what it asks. The risk the plan names, "a
reader missed", is a reader that treats a figure as a paper; this list is
its check.

**Lists the library** (`listed`, `SQL_LISTED` / `SQL_IN_LISTING`):

- `GET /api/blocks/root/children` for a member: `SQL_IN_LISTING`, a page
  dropped whose top is not among the rows; its answer carries
  `work_counts` (`{directory folder: files}`). A folder share lists the
  pages it reaches, a work folder's files among them. `?within=<folder>`
  lists a folder's pages whatever their flag (the HTTP vault reads it).
- `block_search(q="")`, the `[[` popup's recent pages (`IN_LISTED`).
- `ai_tools._run_list_pages` at the root (a file not in the library only
  where the chat's folder or the `folder` argument files it, a page of the
  chat's context always), `list_labels` and `label=`.
- `clip.library_folders`: the folders offered are the filing targets, the
  counts listed pages.
- `folder_sync._page_rows` for the whole library, its `dirs` leaving out
  every W subtree.
- On the client: the library's root, labels' counts, the pickers' default
  lists (`_listed`), the pinned strip from the listing.

**Paper semantics** (`library_document`, `SQL_LIBRARY_PDF`,
`is_library_pdf`):

- `page_for_doc`, `pages_for_docs`, `get_or_create_doc_page` (no backfill
  of a file's doc), by-doc, `POST /pages/by-docs`, the attach's 409
  (`_held_elsewhere`).
- `clip.find_page`.
- `metadata_status`, `POST /metadata/fetch` (409 for a file not in the
  library; an Opened files PDF may still be looked up by hand),
  `POST /jobs/metadata` (`skipped`, counted when there are some; a batch
  of no library document is 400). Both leave out Opened files PDFs too.
- `zotero_preview`, as its commit does.
- `ws_backup`'s live documents and its merge, on both sides.
- On the client: the record lookup on open (`isLibraryPdf`, so an Opened
  files PDF is looked up by hand only), and the Paper metadata button,
  which a work folder's file not in the library does not show.

**Reaches every live page** (no change): block search by ids and backlinks
(`IN_PAGE`), `root_pages(conn, "")`, `POST /api/pages/summaries` (the pages
the listing does not carry, by id), the search index and its readers, the
share scopes, comments, the Inbox, reports, translation memory,
publishing (a hosted plan's page cap counts files), the AI's scoped reads
and its context, the MCP tools, the trash's readers (an entry carries
`work_folder`, "" for a library page, `deleted_by_vault` and, for a file,
`restorable`, whether a person's restore passes, `filing.may_restore`),
seeding, the upload references and `page_family`.

**A folder's pages** (no change, or `live_pages` for the whole library):
`pages_in_folder`, the vault's layout (a page of another W left out), the
folder exports, the bibliography, Obsidian and the Markdown zips, a folder
manifest, `GET /api/folders/{id}/pages` (a work folder's directory, paged;
no row names a parent, a file being inside no page).

**Writes as a person** go through the op rule: `POST /blocks` at the root,
`POST /api/pages`, `POST /blocks/by-doc` (403 `locked` for a new page in a
work folder), the AI's `move_page`, `rename_page`, `delete_page`,
`restore_page`, `rename_folder` and `delete_folder` (refused up front with
the rule's words), and a backup merge, which is also how a Gamma export is
imported: it drops every stamp and mark (the vault that wrote them is the
backup's computer's), keeps only library folders on the pages it adds,
and never makes or merges a folder inside a work folder or into one by
its name (a backup folder that would land there stands for the folder it
would land in). A clip into a folder of a work folder by its id is
refused up front (400 with the `directories` words), and so is an import
into a work folder or one of its directories (`import_review.destination`).

## Known gaps

- Quick open finds a file only from the open tabs and the recents until
  the Files window (step 5); a typed query finds files through search.
- The search panel's folder chip on a work folder sees the pages the
  client knows, until the server resolves a folder scope (step 4).
- Label views list library pages only.
- A vault of another server is never a work folder in step 1.
- A deselected file's last edits are pushed until the next whole round.
- A note's link to a file of another format names its stored address
  (`/api/uploads/<hash><ext>`), a chip, never a mention of the file's page;
  links by path are step 5's. The chip's *Add to library* stores the
  bytes it names and makes a library page of them, a page apart from the
  file's own (`POST /api/pages/by-file`), rather than adding the file's
  page. A picture replaced on disk leaves a note's
  embed of the old address reading nothing until the note's own text
  changes.
- The keep limit is an environment setting (`GAMMA_VAULT_KEEP_MB`), not a
  setting in the app.
- A clone from before step 1 shows files in its library until it upgrades:
  degraded, not lost.
- A work folder still choosing runs no round, so a note made in Gamma in
  one that pulls waits, asked again by the hot loop every `HOT_S` (2 s),
  until the Save.
- The preview and the Save walk the whole directory with no budget: a
  huge directory that is no repository (a home folder) makes them slow.
  Only the tree has a budget.
- On a clone, a file its vault sent to Recently deleted carries no
  `deleted_by_vault` (the mirror's trash writes none), so a note there
  offers Restore; its vault sends it to Recently deleted again while its
  file is left out.
