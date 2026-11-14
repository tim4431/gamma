# Any file as a page: work folders, a file explorer, and feedback to local agents

Asked 2026-11-10: can Gamma stop being a PDF reader with notes and become
a general knowledge base over a person's work folders (a LaTeX repository,
a research repository, code being reproduced)? Every file would be listed
as in a file explorer: PDFs and Markdown notes in the views they have now,
any other text file (`.tex`, `.json`, `.ts`) in a plain code view with
syntax colours, a binary named as a file that cannot be shown. The chat
would read all of it, and what the person writes about it would go to
local agents (Claude Code, Codex) as structured documents. The owner
offered a rewrite from the bottom up and asked first whether the idea is
possible at all.

Status: **a plan; steps 0 to 6 are built** ("As built (step 0)" to
"As built (step 6)" under "In steps"), the rest is not. The owner's
corrections of 2026-11-14 replace layer 7's Send and the Feedback dialog,
and ask for the explorer's look and a Detach:
[agents-as-participants.md](agents-as-participants.md), a plan. This note surveys
what the stored model and the code assumed before step 0, weighs three shapes a
file could take, and lays the chosen one out in layers from the bottom,
with the steps, the decisions still open and the risks. It was revised on
2026-11-11 after the owner's answers ("Decided" below). The mechanics as
built live in [dev/documents.md](../dev/documents.md) (a page's document),
[dev/work_folders.md](../dev/work_folders.md) (work folders),
[dev/block_centric.md](../dev/block_centric.md),
[dev/user_db.md](../dev/user_db.md), [dev/home_library.md](../dev/home_library.md),
[dev/synced_vaults.md](../dev/synced_vaults.md),
[dev/comments.md](../dev/comments.md) and
[dev/highlight_relocation.md](../dev/highlight_relocation.md) (a text
file's ranges placed again).

## The answer in one paragraph

Yes, and it needs no new foundation. The block model already says that a
page carries one *document*, and names the document's kind in the one
function that reads it; the code only ever lets that kind be `pdf`. Make
the document typed and a file of any kind is a page, which gives it a tab,
notes beside it, comments, a chat, a share link, a place in search, the
trash and backups with no code of its own. A synced vault's folder becomes
a *work folder*: the files the person picked, each a page at its path,
kept apart from the library's papers. The feedback the question asks for
is already half there too: a comment thread on a PDF highlight can be
replied to and resolved through the API by a token. What is new is a
registry of formats, the choice of what a vault tracks, comments made
readable as a document for an agent, viewers for text and images, text
files in the search index and the chat, and a Files window. A rewrite
would rebuild the parts that are already right (the op log, rooms, shares,
the mirror, trash, comments) to reach the same shape.

## Decided (2026-11-11)

The owner accepted the first version's recommendations and added what
follows; the design below is written to these.

1. **A file is a root page that carries a typed document**, not a file
   table beside the pages and not blocks of a project page.
2. **A work folder's files stay out of the library's lists**; they are
   found by their folder, by search and by quick open.
3. **Files other than notes are read-only in Gamma** to begin with. The
   disk and the agents write them.
4. **Gamma is a feedback window, not a copy of the repository.** Building
   figures, running simulations and compiling LaTeX happen in the local
   environment. Usually only part of a directory is tracked.
5. **The first use** is a paper being written: the person comments on the
   compiled PDF and on figures, a local agent makes the changes and
   rebuilds. The connection to the agent is the owner's to build later;
   this plan has to leave the right seam for it.
6. **What a vault tracks is chosen** in a dialog like the import review's,
   and can be said in a `.gammaignore` file.
7. **A work folder's PDF is not a library paper** unless the person adds
   it to the library ("Library documents and work-folder files").
8. **Clean shapes over compatibility.** Stored data is migrated, and logic
   that exists only for an old shape goes.
9. The code view is CodeMirror 6, a Files window joins the dock, and the
   words are *work folder*, *document* and *format*.

## What it is for

One loop, which sets the order of the steps:

1. The person makes a directory a work folder and picks what Gamma
   tracks: `main.pdf`, `figures/`, perhaps the `.tex` sources.
2. They read the built PDF in Gamma and comment: on a passage, on an area
   (a figure), on a figure's own file.
3. *Send*: the open comments become a document an agent can act on, each
   with its file, its place, the quoted text, the source line where one is
   known and a picture of the region.
4. The agent edits the sources, rebuilds, replies and resolves.
5. The rebuilt PDF takes the old one's place in the open window, the
   comments are found again in it, and the Inbox says what was resolved.
   The person accepts or reopens.

Of this, today: step 2 on a PDF's passages and areas (comment threads on
highlights, drawn in the margin beside the page), the first half of step
5 (a PDF read in place is replaced when its file changes and its
highlights are placed again; the Inbox tells of a reply and a resolve),
and the API an agent would answer through. Steps 1, 3 and 4's reading
side are missing.

## What exists, and where it stops

The survey of 2026-11-10, before step 0, kept as it was found. Step 0
replaced what "A page and its one document" describes, the vault PDFs'
`linked_file` flag with its `NOT_LINKED` exemption, `storage`'s extension
tables (the format registry) and the viewer chosen twice in the client
(`documentView`, `DocumentPane`); [dev/documents.md](../dev/documents.md)
has them as built.

Sizes, for scale: `backend/gamma` is 228 modules and about 130,600 lines
with 262 test files; `frontend/src` is about 115,800 lines, of which
`app/App.jsx` is 14,591.

### A page and its one document

A page is a row of `unified_blocks` under `root`. It may carry one
document: `properties.doc_id`, the first 24 hex characters of the file's
SHA-256, which names the stored file `<doc_id>.pdf`.
`blocks_store.page_attachment(props)` is documented as "the one read of
what a page carries" and returns `{"kind": "pdf", "id", "url", "name"}`
(`blocks_store.py:436`); its twin is `pageAttachment` in
`frontend/src/library/libraryUtils.js`, and `tests/shared/attachment.json`
holds both to the same answers. So a kind field exists on both sides. It
has one value, and nothing branches on it: `ai_tools.py:516` prints it.

What a page stores of its document is five flat keys with rules between
them: `doc_id`, `source_url` (stored only when the file comes from
elsewhere, derived otherwise), `original_filename`, `linked_file` (the
bytes are at a path on a computer), and for a duplicated page an upload
address alone, from which `attachment_file` parses the id back.

"PDFs are uniquely indexed" is three separate rules:

- **One library page per PDF.** `page_for_doc` finds the page that carries
  a hash, and attaching bytes another page carries answers 409.
- **Derived data is keyed by the hash.** Everything in `data.db` but the
  covers: `pdf_docs` (the viewer's manifest), `pdf_fts` (extracted text
  per PDF page), `pdf_translations`.
- **The suffix is written out.** `storage.upload_refs` turns a `doc_id`
  into `f"{doc}.pdf"` for the orphan bookkeeping (`storage.py:191`), the
  share check matches `.pdf` by the `doc_id` column, and
  `search._index_doc`, `pdf_index.pdf_pending`, `pdf_meta`, `ws_backup`'s
  merge, `folder_sync`, `gamma_sync` and `linked_files` each build
  `<doc_id>.pdf` themselves.

`page_attachment(` is called 35 times in 19 backend modules, `doc_id` is
named 778 times in 62, and `.pdf` 185 times in 51. In the client
`pageAttachment` is called in 7 files, and its results (`_attachment` on a
listing row, `pageAttach`, `tabKinds`) are read in many more.

### Stored files take any type already

`POST /api/upload-file` stores any extension but the executables of
`storage.BLOCKED_EXTENSIONS` as `<sha256[:24]><ext>`, deduplicated,
metered against the quota, and kept or purged by one grammar of
references. `storage.FILE_MEDIA_TYPES` already names `.tex`, `.bib`,
`.json`, `.py` and `.ipynb`. Such a file is content, not an item: it is a
link in a note's text, drawn as a chip (`transfers/FileChip.jsx`) whose
click hands the address to the browser. Nothing in the app previews it,
no index reads it, and the chat sees only the link text. A PDF or a
Markdown chip can be promoted to a page ("Add to library"); no other type
can.

### A synced vault knows three kinds of file, and makes library pages

A vault pairs a folder with a directory on the computer the desktop app
runs on. `vault_disk.kind_of` sorts files by extension into `notes`
(`.md`, `.markdown`), `pdf` and `picture`; anything else adds one to
`Scan.untracked` and is forgotten, path and all (`vault_disk.py:453`).

- A note is a page whose blocks are the file, both ways, through the lens
  (`md_lens`), which patches the file's own bytes.
- A PDF is a page that reads the file where it is: `linked_file: true` and
  a `linked_files` row in `vaults.db` from the stored name to the path. A
  changed file is hashed again and replaces the page's document, and the
  highlights are placed again.
- A picture a note links gets a row and no page.

Every PDF and note below the directory becomes a page, and each is a
library page like any other: listed in the library's feed, its record
looked up. The code already knows that this is not quite right, one
exemption at a time: a linked page is left out of `page_for_doc`
(`NOT_LINKED`), its replacement is not refused bytes another page carries
(`check_held=False`), its title is never taken from a record, a duplicate
of it is not offered, and an export drops the flag.

What a vault reads cannot be chosen. The scan leaves out dot directories,
`node_modules` and the globs of the marker's `ignore`, which no route
sets; `.gitignore` is not read. `POST /api/folder-links/preview` counts
what a directory holds by kind and lists nothing.

What is generic already: the rows (`vault_files.kind` is free text), the
journal, the trash and tombstones, directory renames, the evidence of a
move for files of equal bytes, and `storage.open_path`, which serves any
name from a linked path. What is not: there is no registry of kinds.
`Round` tests `"notes"` and `"pdf"` by hand in `_classify`, `_read`,
`_push_file`, `_push_new`, `_create`, `_moved`, `_pull_page` and `_place`;
`_read` sends every file that is no note down the PDF path (`%PDF` at the
start, `%%EOF` in the tail), so a `.tex` would wait there for ever; the
remotes have `link_pdf`, `replace_pdf` and `store_pdf` and no call for a
file in general. A picture's row is outside the set a round compares, so
a picture moved or deleted on disk is never noticed. The engine is about
14,700 lines with 16,000 lines of tests.

A file read in place leaves no old version behind. After a rebuild a
highlight is placed by its stored quote alone, and an area, which has no
quote and is otherwise found by the text or the picture inside it in the
old file, can only ride along with its page's other highlights, as an
estimate ([highlight_relocation.md](../dev/highlight_relocation.md#areas)).

### Comments are nearly the feedback

A comment thread hangs on one block: a note, a highlight or the page
itself. It is open or resolved, takes replies, and can be reopened. A
PDF's open threads are cards in a margin beside the page, level with
their highlights. The Inbox tells a thread's people of a reply and of a
resolve. An integration token with the write scope may comment, reply and
resolve through the HTTP API ([comments.md](../dev/comments.md) "Who may
comment").

Three things keep it from being the agent loop:

- **No tool reads a thread.** The chat's context, the agent's tools,
  search and MCP leave comments out; `comments.threads_for` has no caller
  among them.
- **A token signs as its account.** A reply an agent writes with the
  person's token reads as the person's own, and the Inbox, which never
  tells an actor of their own write (`inbox._emit`), tells them nothing.
- **MCP is read-only.** Every tool is marked so, the scope is built with
  `can_write: False`, and the OAuth scope is `gamma:read`. Claude Code and
  Codex connect through the plugin in `plugins/gamma/` and pull. Nothing
  in Gamma sends anything to a local agent.

### Search, the chat and the tools read notes and PDFs

Search has two indexes: `block_fts` over every block's text, and `pdf_fts`
over PDF text by `doc_id` and page number. No other file's text is
indexed. The chat's context for a page is its title, properties, PDF text,
highlights and notes (`ai_context.page_report_section`); a file chip's
content is never read, and the composer takes pictures and PDFs only. The
agent's reading tools are keyed on page and block ids, with a PDF window
(`read_page`'s `pdf_page`, `pdf_offset`).

### The client has no page surface and no viewer switch

The open page is inline JSX in `App.jsx`. `openBlock` decides between a
PDF and plain notes at `App.jsx:6625`, and the viewer is a ternary at
`App.jsx:13381`: the PDF viewer, else the notebook, else "No PDF open.".
`workspace/PagePane.jsx`, which draws the pages of the other tab groups,
makes the same choice again. Kind is a boolean wherever it shows
(`FileGlyph isPdf`, `tabKinds` of `pdf` or `page`).

The library is one request, `GET /blocks/root/children`: every page's
summary, with one preview query per page, and the folder and label trees
(`routers/blocks.py:401`). App holds it all, and quick open, the pickers,
the folder counts and the search panel's filters work on that list in
memory. There is no folder tree in a sidebar, which
[block_centric.md](../dev/block_centric.md) lists as a non-goal. The one
tree with checkboxes is the import review's (`transfers/ImportTree.jsx`):
folders tick whole or in part, a closed folder's rows are made when it
opens, and a list shows 300 rows at a time. A drop on the library takes
PDFs and Markdown and refuses the rest.

For code, the bundle has CodeMirror 6 (`state`, `view`, `commands`, no
language package) and highlight.js's common languages, loaded with the
editor. `vault/FileSourceDialog.jsx` is already a plain CodeMirror editor
of one file with a hash-checked save.

## Three shapes

**A. A file tree beside the pages.** A new table of files and directories
with paths as identity; a page becomes one kind of node, or the notes of
one. This is the rewrite. A file is then not a page, so every subsystem
keyed on a page needs a second path or a new key: the op log and rooms,
the change feed and with it the mirror, publishing and backup merges,
shares and their reach, trash and restore, chats, comments, tabs, recents,
the agent's scope, MCP links, the vault's rows. A tab would hold a page id
or a file reference, a chat bucket one or the other, and each reader would
ask which. That is the mixed architecture the question wants to leave,
made permanent, and the parts it rebuilds are the ones the survey of
[data-model.md](data-model.md) found sound.

**B. Files as blocks of a project page.** [latex-projects.md](latex-projects.md)
proposed it for an Overleaf-like editor: a page is the project, each file
a child block of a new kind with its text as the block's content. It
gives text files the three-way merge and carets for free, and suits files
that are written in Gamma. It does not suit a directory someone else
writes: a file is no page, so it has no tab, chat, share, comments margin
or trash of its own; every file's changes go through one page's op log,
which keeps 300 rows; text lives twice, in the database and on disk; and
a binary has no place in it.

**C. A file is a root page that carries a typed document** (chosen).
Nothing about the page changes. The document it carries gains a format,
settled from its name when the file is attached and stored with it; the
viewer, the text extractor, the kind of anchor and what Gamma may write
follow from the format. A Markdown note stays
what it is, a page whose blocks are its content. This is the reasoning
[sub-pages.md](sub-pages.md) settled on for the same problem: a new thing
that should behave as a page is a root page with a property, because the
code assumes root pages in over a hundred places.

One variant of C was weighed for work folders: keeping their files under
a reserved row of their own beside `root` and `trash`, so that every
existing read of the library leaves them out without being told. It was
set aside. The readers that must take a file as a page (ops, rooms,
shares, trash, the change feed, comments) are the ones that test for
`root`, and restoring from the trash would need a flag anyway; so the
flag alone does the work, and the readers that must leave files out are
named and filtered ("Scale" below).

## The design, in layers

Each layer stands on the ones above it in this list.

### 1. Formats

One registry says what a file is, by its name, with a twin in the client
and a shared fixture holding both to the same answers, as the block kinds
and the page's document are held (`gamma/documents/formats.py`,
`tests/shared/formats.json`; built in step 0).

| Format | Files | Content | Viewer | Text for search and AI | Anchor |
|---|---|---|---|---|---|
| `pdf` | `.pdf` | a document | the PDF viewer | per page, MuPDF | `pdf_position` |
| `markdown` | `.md`, `.markdown` | the page's blocks | the notes | the blocks | a block |
| `text` | source, data, config, logs | a document | the code view | the decoded text | `text_position` |
| `image` | png, jpg, gif, webp, svg | a document | the image view | none | `image_position` |
| `audio`, `video` | mp3, wav, mp4 … | a document | the browser's player | none | none |
| `binary` | everything else | a document | "cannot be shown here" | none | none |

A `text` file also has a language (`latex`, `python`, `json`), from its
extension or its whole name (`Makefile`, `Dockerfile`). A name the table
does not know is settled by its bytes when the file is first read: a NUL
or invalid UTF-8 in the first 8 KiB makes it `binary`, and the answer is
stored as the document's `format`. "Kind" already means seven things in the
code (a block's, a change's, a conflict's, a Zotero object's, a vault
row's, the attachment's, the bibliographic type), so the registry's word
is *format*.

The registry replaces the tables that each answered part of the question:
`storage.FILE_MEDIA_TYPES`, `IMAGE_MEDIA_TYPES` and `INLINE_EXTENSIONS`,
`markdown_zip_import._is_asset_ext` and `FileGlyph isPdf` (in step 0:
the tables moved into the registry, and the other two read it), and
`vault_disk.NOTES` and `PAPERS`, the mirror's `pdf` or `file` label and
`FileChip.KINDS` (step 3). A viewer added later (a
notebook, a CSV table) is one row and one component.

### 2. The document a page carries

A page's document is one property, `doc`, in place of the five flat keys
(built in step 0, [dev/documents.md](../dev/documents.md)):

```text
doc: {
  format: "pdf",       the registry's format, stored: settled from the name
                       (and a name the table does not know, from the first
                       bytes) when the file is attached
  name:   "main.pdf",  the file's shown name; "" when it never had one
  hash:   "9f2c…",     the stem the file is, or would be, stored under: its
                       content hash, or the address's hash for a PDF opened
                       from an address
  ext:    ".pdf",      the stored file's extension; the stored name is
                       hash + ext
  at:     "store",     where the bytes are read from: store, path or url
  url:    "https://…", only at url: the address it is shown from
  size:   482113,      reserved for the work folders' listing, written nowhere
  copy:   true         only at store: a duplicated page, which shows a file
                       it does not own
}
```

`format` and `ext` are stored rather than read from the name: names are
often empty (clips, URL pages, Zotero and zip imports), made up, another
file's or without an extension, and one format has many extensions.
`hash` is the stored file's stem rather than the SHA-256 of the bytes as
they are: a PDF cached from an address is named by the address, a PDF
whose embedded annotations were stripped keeps its name, and a file read
in place is named by its bytes when last read. So every record keyed by a
hash (the PDF manifest and index, highlights' positions, translation
memory, the vault's rows) stays valid across the change.

`at` says in one word what `linked_file`, a present or absent
`source_url` and a missing `doc_id` said together: *store*, a
content-named file in the workspace's uploads; *path*, a file of a work
folder, read where it is (or, on a server that cannot see the disk, a
stored copy of its current bytes); *url*, fetched from an address. A
duplicated page is `copy: true` at `store`: it shows the original's file,
which the AI and the exports read, but is never "the page of that PDF",
and the generated column leaves it out. A stored name is the hash and the
extension, built in one function (`stored_name`) in place of each
`f"{doc_id}.pdf"`. A file of a work folder whose extension Gamma refuses
to host (`run-local.bat`, which an upload refuses) is to be stored as
`.bin`, served as a download, and shown by its own name; the code view
reads the bytes itself and never navigates to them.

`page_attachment` became `page_document(props)`, with the client's twin
and the fixture alongside: the view `{format, language, name, hash, ext,
size, at, url, file, copy}` or None. A page still carries one document.
The work is making each reader say which it means:

- **Any document:** the orphan bookkeeping's references, a share's reach
  to a stored file, the mirror's and an export's file lists, the listing's
  summary, the viewer.
- **A PDF:** the PDF manifest, highlights and their relocation,
  translation, the annotated-PDF export.
- **A library PDF:** one page per document, the record's lookup, the
  Zotero link, the clipper's match (the next section).

Each of them asks for the file the page **owns** (`own_hash`, none on a
copy: the lookups by document, the record, the Zotero item) or the file
it **shows** (`file_hash`, the original's on a copy: whatever reads the
bytes).

One numbered workspace step, 52, rewrites each page that carries a
document and points the generated `doc_id` column at the hash a row owns
(`doc.hash`, copies left out; [dev/migrations.md](../dev/migrations.md)).
It moves the workspace's format, so a clone and its origin pause until
both have run it, which is the existing rule, and the step is its own
normalizer for an older backup or export.

### 3. Library documents and work-folder files

The owner's question: should a synced vault's PDF be handled differently
from a library PDF, with its hash index and the rest, and be linked to
the library by hand? Yes. It is the right cut, and the code has been
making it for a while without saying so: each `linked_file` exemption
listed above is this distinction, applied in one place at a time. Said
once:

| | A library document | A work-folder file |
|---|---|---|
| Is | a reference the person keeps: a paper, a book | a working file at a path: a build, a figure, a source |
| Identity | its bytes; one library page per PDF | its path; the hash is its version |
| Changes | rarely, and a replacement is an event | all the time; a rebuild is the normal case |
| Bytes | stored by Gamma: backed up, mirrored, metered | where they are |
| Record | looked up, cited, linked to Zotero | none |
| Title | from its record, unless the person set one | its file name |
| Listed | in the library | in its folder |
| Derived data (manifest, text index) | kept by hash | the current version's; the last one's goes |

One thing must not differ: the reader. Highlights, ink, text boxes,
comments and the chat are the same code on both, or there are two PDF
features to keep. And the hash stays on a work-folder file. It is no
longer what the file *is*, but it is still the right key for what is
computed from the bytes, the right stamp for "the viewer must load this
again", and what tells a file only touched from one that changed.

**By hand, both ways:**

- **Add to library** on a work-folder file makes it a paper: its bytes
  are stored, its record is looked up, it is listed in the library and
  can be filed and labelled there. It is one page, still at its path in
  the work folder and still following its file. Bytes a library page
  already carries answer with that page, as an upload of them does.
  *Remove from library* undoes it.
- **Save a copy to library** makes a new library page from the bytes as
  they are now and leaves the file's page alone: the version that was
  submitted, kept and annotated apart from the draft that goes on
  changing.

Stored, this is one flag on the page beside `work_folder` (below). The
exemptions go: the rule "one page per document, with its record" reads
"a library PDF", and nothing asks about `linked_file`.

A PDF opened by itself (*Open with*, *Opened files*) is the same thing
outside any work folder: read where it is, in the library only when
added. It keeps its own page and its record's lookup, since a file a
person opens to read is usually a paper.

### 4. Work folders

A **work folder** is a folder with a synced vault: the files the vault
tracks, each a page. Every vault folder is one; there is no second kind
of vault. A file's page carries `work_folder`, the root folder's id, and
`file_name`, and three rules follow:

- **Listed where it is.** The library listing, the recents feed, the
  pickers' default lists and the label counts leave a work folder's files
  out, unless a file was added to the library. They are found by opening
  the folder, by search and by quick open.
- **One place.** A file is filed in exactly one folder of its work
  folder, its directory's, and only the vault's own writes change that
  filing or its name. A file in the library may be filed in library
  folders as well. Labels, pins, notes and comments are free. The op path
  holds the rule beside `_filing_patch`, as it holds a sub-page's.
- **A path.** The folders from the work folder down, then the file's
  name. One pair of functions in `blocks_store`, a page's path and the
  page at a path, is the one rule; the feedback document, the agent's
  tools and links by path read it. No path is stored on a page: a
  directory renamed is one op on its folder block, as now.

Directories stay folder blocks, made as files are filed, so a rename is
one op and a folder's chat and share keep working.

Stored: generated columns on `unified_blocks` for `work_folder` and the
first id of `properties.folders`, with indexes for "a directory's files"
and "the pages the library lists". They are derived from what the rows
store, so the step that adds them does not move the workspace's format.

The vaults that exist become work folders at their first round after the
upgrade, as kept folders were taken onto the engine: their pages get
`work_folder` and stay in the library, so nobody's library changes. A
vault of a directory of papers can keep adding its PDFs and notes to the
library by a switch of the vault; a new work folder starts with it off.

### 5. Choosing what a vault tracks

What a vault tracks is a selection, and the selection is a file:
`.gammaignore` at the vault's root, read as git reads a `.gitignore`
(later lines win, `!` takes a path back, a leading `/` anchors, a
trailing `/` names a directory). It is the one statement of what is
tracked. The marker's `ignore` list, which no route ever set, goes.

- **A vault without the file** tracks notes and PDFs, as every vault does
  now. Gamma writes no file into a directory it was not asked to.
- **`.gitignore` is a hint, not a filter.** A paper's built PDF and its
  generated figures are usually ignored by git, and they are the files
  this is for. What git ignores starts unticked and is marked so.
- **Never tracked**, whatever the file says: dot directories (`.git`,
  `.gamma`), `node_modules`, temporary names, links and junctions, as the
  scan leaves them out now.

**The dialog.** Making a work folder, and *Choose files…* on its folder
afterwards, open one dialog in the web app, built on the import review's
tree: the directory's folders and files with a box each, a folder ticked
whole or in part, each row with its format and size, folders opened
lazily. Filters tick by format (PDFs, notes, LaTeX sources, pictures). A
line under the tree says what the choice comes to: how many files, how
much, and what leaves. Saving writes `.gammaignore`, in the shortest
form that says the selection (a few excluded folders, or everything
ignored and the chosen paths taken back), and runs a round.

The desktop shell still picks the directory, since only the shell may
name a path. It makes the vault paused, with nothing tracked, and hands
the dialog the vault's id; the server lists a vault's directory by that
id, so the web app never sends a path. Cancel removes the vault.

**A change of selection** is the disk's change to the round. A file that
is ignored now leaves like a file removed: its page goes to Recently
deleted with a tombstone, and comes back with everything on it if the
file is ticked again within thirty days. The dialog says which of the
leaving files have notes or open comments. A `.gammaignore` edited by
hand works the same, and the guard that holds a round removing half a
vault holds this one too.

A vault holds at most 20,000 files, a guard and not a target: the
developer's checkout of this repository is about 51,000 files as the scan
walks it today (`venv`, `data`, build output), git tracks 2,216 of them,
and a paper's work folder is a few dozen.

### 6. The vault engine

The change in the engine comes in two parts, and the first changes no
behaviour:

1. **A class registry.** `notes`, `pdf` and `picture` become classes, each
   with what a round needs of it: how a file is read and settled, how a
   new one becomes a page, what a change, a move and a removal do, what
   pull writes. `_classify`, `_read`, `_push_file`, `_push_new`, `_create`,
   `_moved` and `_pull_page` dispatch through it, and the remotes' PDF
   calls become document calls with the format as an argument. The
   existing tests are the gate. The engine's modules move into a package
   of their own in the same change, since most of `Round` is rewritten
   and its doc with it.
2. **The class `file`.** A new file is a page with a document at its
   path, filed in its directory's folder. A changed file is hashed and
   replaces the page's document, as a rebuilt PDF does now; any file
   settles by a stat that has stopped moving, the PDF's tests staying the
   PDF's. A file gone is a page in Recently deleted with a tombstone; a
   move refiles the page. Pull never writes a file of this class. A
   picture becomes a file of this class with a page, which ends the row
   nobody watches.

A file too large to hash and serve (a setting, 64 MiB to start) is
listed with its name, size and time and no bytes: it opens as "not kept
in Gamma", with *Show in folder*. A dataset or a checkpoint can be in the
tree without being read.

### 7. Feedback: comments an agent can act on

**A comment thread on a work-folder file is the feedback.** Nothing new
is stored for it: the thread is open until someone resolves it, a reply
says what was done, reopening sends it back. What is added is what makes
a thread usable from outside.

- **More to hang a thread on.** A passage and an area of a PDF take
  threads now. An ink group does too (an arrow drawn on a figure with
  "move the legend here"), and later a region of a picture and a range of
  lines ("Anchors" below). A thread on the page itself is a remark about
  the whole file.
- **The feedback document.** One reader, `gamma/feedback.py`, turns the
  open threads of a scope (a file, a folder, a work folder) into entries:

  ```text
  {id, file: "main.pdf", format: "pdf",
   where: {page: 3, quote: "the loss is defined as"},
   source: {file: "sections/method.tex", line: 118},
   picture: "img/k3f9a2c1.png",
   comments: [{by: "tim", at: "…", body: "lambda is never introduced."}]}
  ```

  `where` is the page and quote of a passage, the page and rectangle of
  an area, the lines of a text range. `picture` is the region as the
  person sees it, marks and ink drawn, made by the crop the chat's region
  picture already uses; for a figure it is the entry's substance. The
  same entries are served as JSON and as Markdown
  (`GET /api/feedback?scope=`), and for a work folder of this computer
  they are files: `.gamma/feedback/open.json`, `open.md` and `img/`, kept
  current by the vault's rounds. An agent in the repository reads them
  with no connection to Gamma.
- **A source line for a place in a PDF.** Where the build's
  `.synctex.gz` lies beside the PDF, a passage or an area maps to the
  source file and line that made it, and the entry names both. For a
  figure that is the line that includes it. The file is read where it is
  and is never a page. This is what makes a comment on page 3 usable by
  an agent that edits `method.tex`.
- **The agent's hand.** A reply and a resolve go through the comment API
  that exists, with a write token. A comment written through a token
  records the token's name beside its author, shows it ("Claude Code"),
  and counts as someone else's write for the Inbox, so the person hears
  of it. Over MCP, `list_feedback` and `read_feedback` join the reading
  tools, and `reply_feedback` and `resolve_feedback` are offered under a
  scope of their own that can do nothing else.
- **Send.** The person's "go": it records a request (who, when, which
  threads, a line of instruction) in the workspace and, for a work folder
  of this computer, as a file under `.gamma/feedback/requests/`. Threads
  in a request show as sent until they are answered.

**The seam for the connection.** The owner builds the part that starts
an agent. What it finds here: a request appearing (a file to watch, or
`GET /api/feedback/requests?after=`), the entries to act on in two forms,
the comment API to answer through, and a request's state to set (running,
done, failed with a reason), which Gamma shows beside the request. Gamma
starts no process itself.

**After a rebuild.** The new PDF replaces the old in the open window and
the highlights are placed again, as now. Two additions make that hold for
comments:

- **The version a comment was made on is kept.** When a thread is opened
  on a file read in place, that version's bytes are stored, and held as a
  pending highlight holds its file today, until the thread is resolved.
  So an area is found again by the text or the picture inside it, and the
  thread's card can show the passage before and after. A figure the agent
  drew again is another picture; its area then rides along with the
  page's other highlights, and the thread keeps the picture it was made
  on.
- **A thread whose passage changed says so.** A highlight placed roughly
  or not at all after a rebuild is the usual sign that the agent changed
  that text; the card shows it beside the agent's reply.

### 8. Text in search, the chat and the tools

- **Extraction** is a function of the format: MuPDF by page for a PDF,
  the decoded text for a text file. One reader; `search._index_doc` and
  the chat ask it.
- **The index** stays in `data.db`, keyed by the content hash, so equal
  bytes are indexed once. A text file's unit is a run of lines where a
  PDF's is a page, and a hit names the file with its line. `data.db` is
  rebuilt on demand, so its tables are renamed and reshaped with no
  migration step. Search hits gain `source: "file"`; a folder scope is
  resolved on the server, never sent as a list of page ids.
- **The chat's context** for a page with a text document is the text, or
  its head and an outline when it is long, with its path and line
  numbers, and the page's open threads. A file chip in a note is read
  the same way.
- **The tools**: `read_page` takes `from_line` and `to_line`, `list_pages`
  names a work folder's files by path, a path resolves to a page. They are
  reading tools, so MCP offers them by its existing rule.

### 9. Viewers

One component, `DocumentPane`, picks the viewer from the format, in place
of the ternary in `App.jsx` and the chain in `PagePane.jsx`. The notes
stay beside it, collapsed when the page has none.

- **PDF**: `pdf/PdfViewer.jsx`, unchanged.
- **Text**: CodeMirror 6, read-only, in a lazy chunk, with
  `@codemirror/language` and `@codemirror/language-data`, which loads a
  language when a file of it opens, and `@codemirror/search`. It draws
  only the lines in view, so a long file costs what its window costs; its
  selection is already lines and columns, which the anchors need; and it
  is the editor the day files are edited in Gamma. A file past a few
  megabytes shows its head and offers the rest.
- **Image**: the picture with the lightbox's zoom and a region tool.
  Audio and video: the browser's own elements.
- **Binary and listed files**: the name, size and format, *Download* or
  *Show in folder*, and one sentence.

A Markdown note has no document pane: its notes are the file, and *Edit
the file* (`FileSourceDialog`) is its source view.

### 10. The explorer

- **Reads.** A folder's own files, paged; the summaries of given pages,
  for tabs, recents and pins that name a file the library listing does
  not carry; files by name or path in a folder, for quick open; a path
  resolved to a page.
- **The Files window.** A dock window like Notes and Chat, on the home
  view and beside an open page: the folder tree with a work folder's files
  under their directories, opened lazily. It reverses the non-goal "no
  page-tree sidebar"; folders and labels stay the model, and the window
  is a view of them. Built in step 5 (As built (step 5)): closed to begin
  with, on a phone an overlay beside an open page.
- **A folder's view.** A work folder opens as the list view, directories
  first, with name, format, size, date and the count of open comments,
  and the path as crumbs.
- **Quick open** gains a Files group, searched on the server.

### 11. Anchors in text and in pictures

A highlight in a text file is a block with `text_position`: the lines and
columns, the quoted text and a few words either side. A region of a
picture is a block with `image_position`: a rectangle in the picture's
own pixels. Either block's kind is `highlight`; the generated column's
rule takes any of the three positions. Each takes notes and a comment
thread as a PDF highlight does.

When a text file changes, each anchor is found again by its quote:
exactly near its old line, then word by word, then marked lost and kept,
the rule set for PDFs. A picture replaced keeps its regions where they
were, marked as made on another version. Regions were built in step 3
(As built (step 3)), ranges of a text file in step 6 (As built (step 6)):
a range carries the text around it, so its run reads no old file.

## What does not change

The block table, ops and their log, rooms and carets, the change feed,
shares and their reach, chats, the comment threads' storage and rules,
the Inbox, trash and restore, backups and the Gamma export, the mirror's
transport, jobs, the notes index, accounts and workspaces. A file is a
page to all of them. That list is the evidence for shape C.

The PDF reader and everything on a PDF page (highlights, ink, text boxes,
pictures, translation, the margin's comments) is the `pdf` format's: the
same code, reached through the registry. What is a paper's (the record,
citations, the Zotero link, one page per document) asks for a library
PDF. Notes are the `markdown` format's content. Neither is taken apart
and restored.

## What is restructured

With compatibility set aside, these are changed rather than extended.
Each replaces several special cases with one rule.

| Now | Becomes |
|---|---|
| `doc_id`, `source_url`, `original_filename`, `linked_file` and their derivations | `doc`, one object, with `at` |
| `page_attachment` returning a kind that never varies | `page_document` returning a format |
| "has an attachment" as the gate of paper features | "a library PDF" |
| The `linked_file` exemptions (lookup, 409, title, duplicate, export) | the library and work-folder rule, stated once |
| A vault's pages as library pages by default | work-folder files; the library by a flag |
| The marker's `ignore`, the scan's fixed kinds | `.gammaignore` and the format registry |
| `"notes"` / `"pdf"` tested by hand through `Round` | a class registry |
| A picture's row with no page, never compared | a file with a page |
| `pdf_docs`, `pdf_fts` | a manifest and a text index of any document |
| Media-type and extension tables in five modules | the format registry |
| The viewer chosen in `App.jsx` and again in `PagePane.jsx` | `DocumentPane` |
| Comments no tool reads, signed by the token's account | the feedback reader; the token named |

New code is grouped where the old is flat: a `gamma/documents/` package
(formats, the document model, text, anchors), `gamma/vault/` for the
engine when it is refactored, and `documents/`, `explorer/` and
`feedback/` in `frontend/src`. The other backend modules stay where they
are: eight other worktrees are open against them, and the docs cite them
by path.

## Scale

A selection keeps a work folder small, and the design should not depend
on that. A row costs little: one block row and one change-log row per
file, no blocks until the person writes. The cost is in readers that walk
every root page. `parent_id = 'root'`, `IN_LIBRARY` and `root_pages` are
named about 100 times in 38 backend modules, and each is classified once:

| Reader | With work folders |
|---|---|
| The library listing, with its preview query per page | lists the library's pages, by the index |
| Lookup by document, the 409 on attach, the record's lookup, the Zotero link | library PDFs only |
| Search and the agent in a folder scope | reach files; the scope is resolved on the server |
| Ops, rooms, trash, shares, the change feed, comments, backups | unchanged, a page is a page |
| The mirror and publishing | files travel when their bytes are stored |
| Exports of a folder | a file's document is written at its path |

The timing budgets get a library with 20,000 files beside 500 pages, and
the home view, a folder open, quick open and one vault round are held to
them before and after. Other costs to measure before they are trusted: a
scan's stat of every file on each tick, where Windows is slowest and a
native watcher is the known answer; the first round's hashing; the folder
tree's size in the listing.

## What leaves the computer

A work folder holds code, data and sometimes secrets. Three rules, said in
the dialog that makes one:

- A vault of this computer's own server copies nothing but what the
  person asks to keep: files are read in place, a commented version is
  stored while its comments are open, and a file added to the library is
  stored.
- A vault of another server stores every tracked file there. The dialog
  says how many and how much, and the selection is the control.
- The chat sends a file's text to the model provider when the file is in
  its context, as it sends a PDF's now.

Gamma writes into a work folder's directory only below `.gamma/` (the
marker, the trash, the feedback files), the `.gammaignore` the person
saved, and a note through the lens where the vault pulls. It never writes
a file the disk made.

## In steps

Each step leaves the app working and can ship alone. The loop of "What it
is for" works at the end of step 2.

| Step | Builds | Gate |
|---|---|---|
| 0. Seams and the document | The format registry with its fixture. `doc` and its migration step; `page_document` in place of `page_attachment`, every reader naming any document, a PDF or a library PDF. `DocumentPane` taken out of `App.jsx` and `PagePane.jsx`. No behaviour changes. | Every suite as before; a backup from before the step restores. |
| 1. Work folders | `work_folder`, the library flag and their indexes; the listing's filter, the op rule, the root readers classified; a folder's paged read and its list view. `.gammaignore`, the dialog, a change of selection. Add to library, Save a copy. The vaults that exist taken over. On the three classes the engine has. | A paper's directory with its PDF and figure PDFs ticked: none in the library, no record looked up, the PDF added to the library by hand. |
| 2. Feedback on PDFs | Threads on ink; the feedback reader, its routes and its files; the token's name on a comment and in the Inbox; the MCP tools and scope; Send and requests; the SyncTeX map; the commented version kept. | A comment on `main.pdf` answered and resolved by a script holding a token, after a rebuild, with its source line named and the Inbox telling of it. |
| 3. Any file | The vault's class registry with behaviour unchanged, then the class `file`. Attaching and promoting a document of any format. The text, image and media viewers and the "cannot be shown" pane. Regions of a picture, with threads. | `main.tex`, `data.json`, a PNG figure and a `.bin` in a work folder each open as they should; a comment on the PNG reaches the feedback document with its picture. |
| 4. Text in search and the chat | The extractor, text files in the index, `source: "file"` hits with lines, the chat's context and `read_page` by lines. | A question about a `.tex` file answered with a link to its line. |
| 5. The explorer | The Files window, quick open's Files group, paths in crumbs and links. | The browser suite's new group. |
| 6. Anchors in text | `text_position`, highlights and threads from a selection, relocation after a change. | A file rewritten under ten comments keeps the ones whose text survived and marks the rest. |
| Later | Work folders of another server from the dialog (made of an empty folder there: [agent-collab.md](agent-collab.md) step 4b; a folder with pages still later); editing a text file in Gamma; notebook and table viewers; building LaTeX ([latex-projects.md](latex-projects.md), parts 4 and 5, on a work folder's files); opening files and folders from the file manager: [open-with.md](open-with.md). | |

Step 0 and the first half of step 3 are refactors with nothing to show,
and they carry most of the risk; each is small enough to review alone for
that reason. Step 2 needs step 1 and nothing of step 0's client half, so
the two can run side by side.

**As built (step 0).** Built as its row says
([dev/documents.md](../dev/documents.md)). The registry is
`gamma/documents/formats.py` with its client twin, held by
`tests/shared/formats.json`. A page's document is `doc`, and migration
step 52 (`page_documents`, a workspace part only) rewrites every older row
and the mirror's bases. It moves the workspace format from 44 to 52, so a
clone and a publication pause until both sides have run it, a desktop
vault on another server refuses a server of another format, `gamma_sync`
refuses one below 52, and an older `gamma_sync` is updated with the
server. `page_document` and `pageDocument` take the place of
`page_attachment`, `attachment_file` and their twins, held by
`tests/shared/document.json`, and every reader names one of the
predicates: `own_hash` (the file the page owns: the lookups by document,
the record, the Zotero item, the client's `docId`), `file_hash` (the file
it shows: the AI, the exports, the relocation, translation),
`is_pdf`, `pdf_file_hash` (the PDF it shows), and `in_place` with
`SQL_LIBRARY_PDF`, which in step 0 means a PDF not read in place and is
asked only where a page read in place was set apart before. `documents/documentView.js` decides the
viewer for App and `PagePane.jsx` alike, and `DocumentPane.jsx` draws it.
Where it differs from the plan above: `format` and `ext` are stored, not
read from the name; `hash` is the stored file's stem, an address's hash
for a PDF opened from one; a duplicated page is `copy: true`; `size` is
reserved and written nowhere; `original_filename` stays as the name of the
Markdown file a page was made from. The behaviour that reads differently
from the flat keys, on purpose, is this whole list:

1. A malformed stored document (a `doc_id` `db.safe_doc_id` refused) reads
   as no document.
2. `POST /api/blocks/by-doc/{id}` with an id `db.safe_doc_id` refuses
   answers 400, where it was stored.
3. `PUT /pages/{id}/attachment` with a URL on a page read in place puts
   its document at the address, where `linked_file` stayed beside a URL
   (the UI never sends this).
4. A detach of a page read in place leaves no `linked_file` behind.
5. A Markdown zip's PDF page's Document popover shows the address's tail
   or "Untitled" in place of the `.md` file's name; its title is stored
   and does not change.
6. A write that carries a flat key with a value is a 400.
7. Old clients (an open tab, an older gamma-sync, an older desktop app
   against a newer NAS) read no document until updated; the HTTP vault
   and gamma-sync say so.
8. A duplicated page's PDF is read by the AI and the exports, as the
   original's is.

**As built (step 1).** Built as its row says
([dev/work_folders.md](../dev/work_folders.md)). A file's page carries the
stamps `work_folder`, `library` (true or absent) and `file_name`; W's
folder block carries the mark `vault: {pull, library}`. Two generated
columns, `work_folder` and `first_folder`, and three partial indexes
(`idx_ub_listing`, `idx_ub_dir`, `idx_ub_work_folder`) come with migration
step 53 (`work_folders`), which also adds `folder_links.library`; the step
is derived, so the workspace format stays 52. The library rule is
`gamma/work_folders/library.py` with its client twin, held by
`tests/shared/library.json`; the op rule is `gamma/work_folders/filing.py`
with its twin, held by `tests/shared/workfiling.json`, and a refusal is a
403 with the conflict `locked`. `.gammaignore` is read and written by
`gamma/work_folders/ignore_rules.py`; the tracking dialog's routes live in
`routers/folder_links.py` over `work_folders/selection.py` and
`folder_links.save_selection`; the vaults from before the step are taken
over by `work_folders/takeover.py` at their first round. On the client,
`frontend/src/explorer/` holds the rules' twin, the folder's list view,
the pages the listing does not carry and the tracking dialog. Where it
differs from the plan above, the whole list:

1. A work folder is a vault of this computer's server that pushes
   everything, not every vault folder. A vault that only pulls, or pulls
   and sends PDFs back, makes Gamma its folder's source; a vault of the
   whole library has no folder to mark; a vault of another server cannot
   use the dialog yet. These stay kept folders. Turning a kept folder's
   push to everything makes it a work folder, its pages kept in the
   library. A vault of the whole library can no longer be made, or turned,
   to push everything; one that did before goes on as a kept folder. A
   vault of another server that pushes everything into a folder a work
   folder marks there runs a work folder's round, as the op rule there
   asks.
2. Notes in a work folder that pulls follow Gamma: rename, a move between
   its directories, trash and restore, and new notes. PDFs, pictures,
   paper pages and directories follow the disk only. This is "a note
   through the lens where the vault pulls", and it keeps a taken-over
   Obsidian-style vault's renames.
3. Folder operations inside a work folder: its directories follow the disk
   (no rename, move, delete or new subfolder from Gamma); W itself may be
   renamed and moved (its name in Gamma only), never deleted.
4. No sub-pages in a work folder: the takeover flattens them, and the
   front matter's `parent:` makes none.
5. One stored flag, two predicates: `library` exists only on files.
   Outside a work folder the default ("not read in place") keeps Opened
   files out of the paper rules, so nothing asks about `linked_file`, and
   `at` is asked only of pages outside work folders. An Opened files PDF
   therefore has no record looked up when it opens and is left out of the
   metadata status and its batch, where section 3 kept its lookup; it may
   still be looked up by hand (the Paper metadata button,
   `POST /metadata/fetch`).
6. The listing carries pinned files, marked not listed, so a pin of a file
   survives without a pins read.
7. The mark: `vault: {pull, library}` on W's folder block tells every
   server and client which folders are work folders, protects an empty
   one, and carries the two switches the op path and the readers need. The
   plan stored `work_folder` on pages only.
8. Two selections. With no `.gammaignore` a vault tracks notes, PDFs and
   pictures, as before. The dialog's preset for a new work folder is PDFs
   and notes unless they lie inside a directory git ignores: a PDF git
   ignores by a file pattern alone (a paper's `main.pdf` under `*.pdf`) is
   ticked and marked "git ignores it". The dialog's tree is not read a
   folder at a time: it lists the directory whole up to a budget (50,000
   entries, 3 s), and reads a folder left unwalked (past the budget, or
   the outermost directory git ignores) when it is opened.
9. `.gammaignore` is read at the root only, always case-insensitive and
   NFC, unlike git. A missing file after one existed stops the vault
   rather than falling back to the default. A vault whose older marker
   names `ignore` globs gets them written once as its `.gammaignore`, the
   one file Gamma writes into a directory unasked, and a Save of the
   dialog always writes a file, a selection equal to the default too.
10. In a kept folder a deselected file's row is forgotten and its page
    stays: Gamma is that folder's source. Only a work folder sends a
    deselected file's page to Recently deleted, with an in-place tombstone
    that never moves the file.
11. Removing a work folder sends its files not in the library to Recently
    deleted, keeps the library ones, and drops directory folders left
    empty.
12. A library file's versions are stored, each as it comes, as a library
    document's bytes are; its document still reads in place.
13. Add to library sets `auto_title`, so the record may title the paper;
    Remove resets the title to the file's stem. The route stores the bytes
    and schedules the PDF's manifest; the record is looked up by the open
    page, as for any library PDF.
14. A paper's PDF and its notes are tracked together: the dialog joins
    pairs, and a hand-edited half is forgotten, not removed.
15. Save a copy to library is for PDFs; a note has Duplicate.
16. A hosted plan's page cap counts files.
17. Known gaps until later steps: quick open finds a file only from tabs
    and recents (step 5); the search panel's folder chip on a work folder
    sees known pages only (step 4); label views list library pages only;
    the dialog's preview and Save walk the whole directory with no budget,
    only its tree has one ([dev/work_folders.md](../dev/work_folders.md)
    "Known gaps" has the rest).
18. A work folder may not lower its push while some file is not in the
    library.
19. `MAX_LINKS` stays 10.
20. The takeover's stamps are quiet: no `updated_at`, so nobody's recents
    reorder.
21. The vault's exemption is the op client `disk` on a token request: an
    integrity rule, not a security boundary.
22. The two listing indexes lead with `parent_id`
    (`idx_ub_listing` on `(parent_id, position)`, `idx_ub_dir` on
    `(parent_id, first_folder)`), or SQLite's planner takes `idx_ub_parent`
    for the listing and the directory counts. The `work_folder` column is
    NULL for an empty or non-text value, and the SQL reads `library` as
    JSON `true`, so the SQL and the predicates answer alike for every case
    of the fixture.
23. A work folder made in a new folder, or of a folder with no page, is
    one from the start; one made of a folder that holds pages (the API
    allows it while the directory holds no file) is taken over at its
    first round, as the vaults from before the step are.
24. The scale check is `backend/tests/test_work_folder_scale.py`, timing
    tests run with `-m perf`: 20,000 files in one work folder beside 500
    pages, the listing within 1.5 times its time without them, the first
    page of a directory of 10,000 files (60 ms, 90 ms sorted by size),
    `work_counts` (10 ms) and the op rule's read of a tree of 2,000
    directories (16 ms). Quick open and a vault round are not timed in
    this step.
25. The cap of 20,000 files (`selection.MAX_FILES`) is a work folder's:
    the dialog's Save refuses more, and a round that selects more stops
    before it writes; a kept folder has none. The takeover raises a
    vault's cap to the files it already tracks, so no vault stops at the
    upgrade.
26. The dialog does not say what leaves the computer yet: a vault of
    another server is never a work folder in step 1, and one of this
    server stores a file's bytes only when the person asks (Add to
    library, then each version; Save a copy) or removes the vault, whose
    PDFs read in place are stored first, as any vault's are.
    Besides its notes, pull writes into a work folder that pulls the
    pictures a note made in Gamma links, where the selection tracks them,
    as every vault does.

**As built (step 2).** Built as its row says
([dev/feedback.md](../dev/feedback.md)). A thread's root stores where it
was opened (`comments.anchor`: the kind, the block, the file it was
measured on, the page, the rects, the quote, the picture's crop and the
source recorded then), and every comment the token that wrote it (`agent`,
`agent_id`; `resolved_agent` on a resolve), with migration step 54
(`feedback`), which is local (`LOCAL_STEPS`), so the workspace format
stays 52. The reader is `gamma/feedback/` (`entries` the scopes, entries
and document; `markdown` the Markdown form; `pictures` the region
pictures, cached in data.db; `kept` the commented version held;
`synctex` and `sources` the SyncTeX map; `requests` a Send's store and
states; `files` the files below `.gamma/feedback/`; `listeners` what
marks them; `agent` the answering tools), its routes `routers/feedback.py`
under `/api/feedback`, its MCP tools `list_feedback`, `read_feedback`,
`reply_feedback` and `resolve_feedback`, and the token scope `feedback`
(`gamma:feedback` on OAuth, through a consent checkbox). On the client,
`frontend/src/feedback/` holds the dialog and Send, `comments/` the ink
threads and the Sent chip. Where it differs from the plan above, the
whole list:

1. The step is 54, after dev's 51 (`integration_workspaces`), step 0's 52
   and step 1's 53.
2. The entry is wider than the plan's example: `source` gains `end` and
   `version` (`current` or `commented`); `where` gains `box`, `placed`,
   `match` and `note`; the entry gains `kind`, `page_id`, `block_id`,
   `before`, `state`, `sent` and `link`; comments gain `id`, `agent` and
   `deleted`; the document gains `owner` and `answer`.
3. Pictures are `.jpg`, drawn at 1568 px, never uploads.
4. Every build shown while a thread is open is stored, not one copy per
   round of comments: a second rebuild would otherwise lose the area's own
   file. A stored copy of a file read in place never goes to a mirror, a
   share export or a publication; backups and the off-site copy carry it,
   and the quota counts it.
5. The reader covers work-folder files only; a library page's threads are
   not feedback.
6. Ink threads are not relocated: after a rebuild an ink entry reads
   `placed: "earlier"` and leads with its `before` picture.
7. The source is recorded at the thread's open (`anchor.source`), because
   the `.synctex.gz` on disk describes only the newest build; a live source
   needs this computer's disk, so the NAS, a clone or a vault of another
   server show recorded ones only.
8. The files are written at every whole round and within a hot pass (about
   2–4 s) of a change, not by the rounds alone; a paused or choosing vault
   writes none; `.gamma/feedback/` has its own `.gitignore`. A new work
   folder's first round writes an empty document.
9. A token is named on what it writes, and a token edits and deletes only
   its own comments; a session cannot edit an agent's comment. A token's
   comment makes no one follow the page, and counts as someone else's
   write for the Inbox, a `write` token's script included: its items go
   to the thread's authors, the page's followers and the people it names.
10. The answering tools are MCP-only and listed for a `feedback`
    connection alone; a `write` token answers through the HTTP API, as
    before, and so does a `feedback` token, in its own workspace. The 401
    challenge still names `gamma:read`.
11. Requests have four states (`sent`, `running`, `done`, `failed`); the
    requester may withdraw; a request file is `requests/<id>.json`,
    rewritten on each change, its images shared with `img/`. The requests'
    sweep runs on the comments' timer.
12. Comments by other people are feedback too; the skill acts on the
    owner's and asks about the rest.
13. Comments made on another server (the NAS, a phone's clone) never reach
    the desktop's files or its agent: comments do not travel.
14. Not built: a figure's own file mapped to the line that includes it;
    widening an `\end{…}` line to its `\begin{…}`; the `\includegraphics`
    path in `source`; re-finding a vector figure by its SyncTeX line after
    a rebuild; an Inbox item when a request is done; a palette command for
    Feedback. An area is found again by the picture inside it only for
    raster images.
15. A split made for an abandoned Comment on handwriting stays split.
16. The feedback view is one dialog with Send, opened from the list view's
    bar, the Comments window and the VaultChip card; it polls while open.
17. The Markdown files are in English (they are for agents); everything a
    person reads in the app is translated.

**As built (step 3).** Built as its row says
([dev/synced_vaults.md](../dev/synced_vaults.md) "The classes",
[dev/work_folders.md](../dev/work_folders.md),
[dev/documents.md](../dev/documents.md) "Writing a document" and
"Viewers", [dev/feedback.md](../dev/feedback.md)). The vault engine is the
package `gamma/vault/` (`kinds` the class registry, one object per row
kind, `class_of` deciding by name and never by bytes; `remote` what both
remotes and the round share; `local_remote` and `http_remote`; `sync` the
round; `disk`, `store`, `merge`, `links` and `upgrade` as they were), no
module named `vault_*.py` left and no shim; the remotes' calls are
`link_doc`, `unlink_doc`, `replace_doc`, `store_doc` and `set_doc`. The
class `file` is every file a work folder's selection takes that is no
note or PDF, pictures included: a page titled by its leaf, filed in its
directory, its document read where it is (`at: "path"`, with `size`) or,
past `GAMMA_VAULT_KEEP_MB` (64), listed (`at: "listed"`, no hash, no
bytes); a touch with the same bytes refreshes the stat; a rename
re-settles the format and extension. In the library a stored file of any
format is a page (`POST /pages/by-file`, `attach {file}`, `by-docs
files`), one page per stored file by hash and extension
(`page_for_file`), its format settled on the server (`stored_format`),
`PUT /attachment` keeping the page's format; the record, Zotero, the
clipper and the AI's PDF tools stay the PDF's. The client's viewer registry
is `documents/viewers.js` (`FORMAT_VIEWER`, `VIEWERS`: each pane's tools,
ink, regions and words, which App reads and never a viewer's id);
`DocumentPane` mounts the code view (`viewers/TextViewer.jsx`, CodeMirror
read-only, a head-first load, language chunks from `language-data`), the
picture (`viewers/FileViews.jsx` `ImageViewer`: regions, the comment
margin, `viewerZoom`), the player and the file pane from one surface
(`documents/viewerSurface.js`), each a lazy chunk. A region is a highlight
block with `image_position` (`{width, height, boundingRect, rects, hash}`,
the picture's own pixels); the kind column's `highlight` case reads
`pdf_position`, `image_position` or `text_position`, rebuilt by migration
step 55 (`positions`), which moves the workspace format to 55. Step 2's
feedback takes regions: the anchor gains `ext` and the kind `region`, the
entry `where: {box, rect, size, placed}`, the pictures a region's crop and,
for a thread on a picture's page, the whole picture
(`ai_pictures.image_region`, the chat's picture of a picture page too).
Where it differs from the plan above, the whole list:

1. The class `file` exists only in a work folder's round. A kept folder
   (Gamma its source) keeps the picture class and its page-less rows:
   making every attachment of an Obsidian-style vault a library page would
   fill the library. Pictures in every existing work folder, and in every
   vault taken over in step 1 (whose default selection tracks pictures),
   become pages at the first step-3 round, their old picture rows becoming
   those pages' rows once.
2. The library switch adds notes and PDFs only; a work folder's pictures
   and other files never join the library by it.
3. A file page is titled by its whole name (`main.tex`, `fig.png`), so
   `main.tex` and `main.pdf` differ in tabs and quick open; a PDF keeps its
   stem.
4. The listed state is a fourth `at`, `listed` (no hash, `size` kept), and
   it moves the workspace format with step 55 (an older server's op path
   refuses it, and its kind column reads a region as a note). The file's
   time is not stored: its pane and the list view show the page's
   `updated_at`, which the vault moves when the document changes.
5. The keep limit (`GAMMA_VAULT_KEEP_MB`) applies to the class `file`
   only, never to PDFs or notes; it is an environment setting, not an
   admin setting yet. The tracking dialog's preview answers `large` and
   `keep_bytes` in place of `unread` (nothing is unread now).
6. Notes link a file page by its stored address, as before
   (`![](/api/uploads/<hash>.png)`), not as a mention of its page; links by
   path are step 5's. `LinkContext.pages` stays notes. In a work folder the
   link context reads file rows and the scan's files with no row yet, so a
   note that links a picture new in the same round keeps its link; the
   page-less rows a file page's row now serves are dropped after the push
   phase.
7. Removing a work folder stores nothing for a file page (the PDFs read in
   place are stored as step 1 built): a trashed file page reads its file as
   missing unless it was in the library or had an open thread. Releasing a
   vault forgets its file rows; their pages keep their documents.
8. "One page per document" holds for every format in the library, keyed by
   hash and extension (equal bytes under two extensions are two stored
   files); the record, Zotero and the clipper stay library PDFs. `PUT
   /attachment` keeps the page's format (400 otherwise); the vault's
   replacement may change it (`replace_attachment(keep_format=False)`),
   and highlights are relocated only between two PDFs. `by-file` answers
   `{page, created}`; an Untitled page attached a file of any format is
   titled by the file's name, `auto_title` set for a PDF alone; a stored
   PNG asked for its record is 409 `NO_PAPER`; a listed file's Add to
   library or Save a copy is 409 `TOO_LARGE`.
9. `FileChip.KINDS` stays as the chip's tooltip words, finer than formats;
   no decision reads it. The chip's lookup is keyed by stored name
   (`by-docs files`, a Markdown name answered by its note page too), and
   `uploadFilesAsLines` sends every non-picture file through
   `uploadAnyFile` (parts past 32 MiB); `.avif` and `.bmp` are inline
   pictures now that they have rows, `.tiff` and `.heic` chips.
10. The image view's zoom is `viewerZoom.js`, the PDF's and the notebook's,
    not the Lightbox's, and its fit leaves room for the comment margin
    (`marginLayout.fitBeside`). Its DOM is `.imgLayer` (the zoom layer at
    the scroller's origin) holding `.imgBox` (the picture's box), as the
    PDF's layer and page wrap, since the pinch math needs the layer at the
    origin.
11. One region drag (`shared/ui/regionDrag.js`) for the PDF's area and the
    picture's region; on a picture a mouse or pen drag draws with no key
    held, a finger only with the strip's toggle. The tip after a drag: a
    swatch makes the region in that colour, Note in the palette's first,
    Comment opens a thread, Ask AI asks the chat.
12. The code view's Wrap is session state (wrapped by default on a
    phone), not a stored preference; its search is CodeMirror's panel (the
    search commands decline inside `.docTextView`), and the search panel's
    "this page" does not reach a code view (step 4 indexes files). A−/A+
    buttons were not built: `useTextScale` has no setter and no other text
    panel has them; Ctrl+wheel scales the text. A file the server refuses
    (a work folder's file on another computer) hands over to the file pane
    as a binary does.
13. `image_position.hash` is always written and holds nothing; "made on
    another version" is derived on both sides, so a replacement writes no
    op on regions. Step 2's anchor holds the commented version. On the
    client `blockPosition(b, key)` reads any key but `image_position` as
    the PDF's.
14. Step 55 rebuilds the kind column with `text_position` already (step
    6's), to save a second rebuild.
15. The feedback document gives a thread on a picture's page the whole
    picture, and a region on another version its current crop (`placed:
    "earlier"` with a `picture`; "the picture was rebuilt" when a `before`
    stands beside it), where step 2's rules gave neither. `pictures.Region`
    names the stored file (`<hash><ext>`), a PDF's key still hashed by its
    stem so no work folder redraws its `img/` at the upgrade;
    `image_region` answers `render_page`'s 4-tuple; a region drawn on
    another version is drawn like the others on the current picture.
16. A sniffed PDF: attached in the library it is stored again as `.pdf`
    and becomes a PDF; in a work folder (no `.pdf` name) it is a binary.
17. A dropped directory uploads every file but the never-tracked ones and
    blocked extensions, asking first past 100 (`transfers/fileRouting.js`,
    the pure rules; `documentUploads.js` the calls). A single uploaded
    document opens its page as a Markdown import does, not with a viewer
    URL.
18. `/upload-file` keeps a name's extension only when it is an image row
    of the declared media type (`photo.jpeg` with `image/jpeg`); `.ico` is
    `image/x-icon`. `settled(path, tail=None)` skips the `%%EOF` test for a
    file; the same-bytes restat and `drop_pageless` are remote calls
    (`restat_doc`, `drop_pageless`), no-ops over HTTP; `_new_files` in a
    work folder writes a linked picture with no journal and no row, so the
    next whole round makes its page; the HTTP remote's upload memo is
    keyed by stored name.
19. The engine's split keeps `_token`, `_why` and `_refused` in `sync.py`
    and `NO_ACCESS` in `local_remote.py`; the copy-out of a page's stored
    PDF stays in `Round._pull_page` before the class dispatch, gated on
    `info.format == "pdf"`; `Round._place` dispatches to `Kind.place`;
    `Kind.left` runs per class, notes before PDFs; `class_of` decides on
    the leaf (`a/b/.md` is no note), its `work` keyword defaulting to
    False; a file's move with a new format is re-settled in `Round._moved`,
    where the page is known live; `FILE.pull` records seq, title and folder
    so the next round counts the page unchanged.
20. The ink-threads' and regions' words on the client: `showThread` lands
    on the block (`landOnBlock`), which scrolls the PDF to a highlight's
    passage too; `DocumentPane` is keyed by the page while a pane of its
    own shows, so a picture to picture switch mounts fresh; a region's row
    crop retries while the picture loads; the Document menu reads "Show
    the file" for a non-PDF document; the attach command is "Attach a
    document…" on a page with a document of any format; the 409 on attach
    says "That file is already in the library as {title}". `NoteBadge`
    lives in `comments/` (prop `onMenu`), so the picture's chunk never
    imports pdf.js. `documentFileUrl(doc, {share, asset})` takes the host's
    `assetUrl` rather than importing it (node tests the model).
21. The review of a backup's merge and an import labels a page by its
    document's format (`image`, `text`, `pdf`), the client showing the
    format's word.
22. The browser group is `viewers`; its share step is in the group
    `notes-pdf-share` (there is no `share` group); `clip.mp4` is WAV bytes
    so the pane's pause can be asserted on a playing element.
23. Not built in step 3: text in search and the chat (step 4); the AI's
    area pictures of a region in `read_block` and the context
    (`ai_context.area_highlight` stays PDF); exports writing a work
    folder's file at its path; a region crop in the readable exports (a
    region exports as a note); editing a text file; Opened files of
    formats other than PDF (built since by [open-with.md](open-with.md),
    which also decided that a Markdown file outside every vault asks
    whether to become a note of a work folder, else opens read-only in the
    code view, and is never added to the library); a listed file's head as text; a region's own
    rows in the highlight menu (the PDF's rows show); the AI title for a
    non-PDF document (`docId` is the PDF's); an admin setting for the keep
    limit.

**As built (step 4).** Built as its row says
([dev/documents.md](../dev/documents.md) "Text extraction" and "Viewers",
[dev/api.md](../dev/api.md) "Search", [dev/ai_context.md](../dev/ai_context.md)
"A text file", [dev/ai_tools.md](../dev/ai_tools.md),
[dev/mcp.md](../dev/mcp.md), [dev/pdf_citations.md](../dev/pdf_citations.md)
"Line links", [dev/work_folders.md](../dev/work_folders.md) "Paths"). The
one reader of a document's text is `gamma/documents/text.py`: `extract` by
format for the index, `decode`, `read_lines` (a window streamed, the rest
of the file counted from its bytes), `outline` by language, `locate` and
`is_secret`; its decode rule is the code view's too, held by
`tests/shared/textdecode.json`. The index is the package `gamma/search/`
(`index` the tables and queries, `indexer` the lazy `indexing` job,
batched, `hits` the one hit builder) in place of `pdf_index.py`, with no
shim: data.db's `text_fts`, `text_fts_docs` and `text_fts_rows`, keyed by
hash, a PDF's unit its page and a text file's a run of 40 lines with each
line cut at 500 characters, so a run and a page are units of one size
and rank together. An index of the old layout is dropped at its first use
and filled again by the job; there is no migration step, and the
workspace format stays 55. A file hit is `{source: "file", path,
work_folder, language, line, lines, snippet, …}`, its line found again in
its run by FTS5's own token rule. `POST /api/document-search` is the
panel's document group; folder and label chips go to it and to
`/block-search` as ids, resolved on the server
(`routers/search.scope_pages`), so a work folder's chip reaches its
20,000 files. A path resolves to a page through
`gamma/work_folders/paths.py` (`page_path`, `page_at_path`,
`work_of_path` and the name rule `work_folders_named`), on which
feedback's file names are built too. The chat's context for a text
document is its numbered lines, whole or a head and an outline from the
first line not shown (`ai_context.text_head`, cached per file version),
with the page's open threads and a note's file chips; `read_page` reads by
`from_line` and `to_line` or by `path`, `list_pages` names a file's path,
and `search_library` lists file hits. The line link
`/?page=<id>&line=N[-M][&quote=…]`, its quote the first line's verbatim
text, is a fourth kind of Gamma link (`gammaLinks.js`,
`mcp_links.resolve_link`), drawn as the citation's pill and opening the
code view at its lines, marked (`documents/viewers/textMark.js`). The
browser group is `text-search`. Where it differs from the plan above, the
whole list:

1. The chat's PDF reader stays `pdf_text` (`pdf_excerpt`, labelled,
   cached): the one extractor fronts the index for every format and the
   chat's and tools' reading of a text file, and the chat's PDF path keeps
   its page labels and its cache, which a library-wide index must not
   share.
2. The path pair lives in `gamma/work_folders/paths.py`, not
   `blocks_store`, which that package imports. The work folder name rule
   (exact, then ignoring case by `blocks_store.named`'s `lower()`) is
   stated there, and feedback's lookup is built on it, its `casefold()`
   gone.
3. A listing-side predicate (`year:`, `author:`, `is:pinned`) still travels
   as page ids. Only a chip, or a `folder:` or `label:` clause naming one
   folder or label, is a scope sent as ids: a negated clause, or one naming
   several, stays a predicate, since the server intersects its ids. Beside
   a predicate a work folder's chip leaves out the files the library does
   not list ([dev/work_folders.md](../dev/work_folders.md) "Known gaps").
   There is no `is:text`; `in:files` is the files' switch.
4. The index's unit column is `unit`, with no line columns: a run's lines
   follow from its number and `RUN_LINES`, and the matched line is found
   by re-scanning the run on the server (`text.locate`).
5. `/pdf-search` (GET and POST) is gone with no shim; `GET /api/search`
   keeps its shape and gains file hits; the two routes and `search_library`
   share `search/hits.py`. A PDF hit names its document's first page in
   `GET /search` and every page of it in `/document-search`; a file hit
   names every page whose document it is, so two files of equal bytes are
   two hits.
6. No "This file" find group for the open code view: CodeMirror's panel is
   its find, and the Files group lists the open file's hits, tagged *this
   file*.
7. A cited file page the reader never opened has no source name on its
   pill: the model is told to write the file's name in the link's text,
   and the preview says "This file".
8. The composer takes no text files. A note's file chips are read, three a
   page, each its first 4,000 characters with no outline (an outline would
   read the whole file); the tracking dialog says what leaves the computer.
9. Threads of every page are in the context, not only a text document's,
   placed from the thread's stored anchor as the feedback entries are.
10. The outline goes to the plain chat too; the call to read on with
    `read_page` is written only for a chat with tools.
11. The decode rule is UTF-8 with replacement after a BOM check, a NUL
    anywhere or more than 5% U+FFFD making a file no text, for the server
    and the code view alike (the code view's first-8-KiB test is gone); no
    code-page guess.
12. A secrets file's text never reaches the model, the index or a chip
    (`text.is_secret`: `.env` and `.env.*`, `.netrc`, `.npmrc`, `.pypirc`,
    `.htpasswd`, the ssh keys, `.pem`, `.key`, `.p12`, `.pfx`); the code
    view shows it. This is the one exception to "What leaves the computer"'s
    third rule.
13. The indexer still downloads a PDF at an address, as before, through the
    chat's guarded resolver: a search finds what the page shows.
14. The scoped search's budget is 800 ms, not 150: each request reads every
    file page's document (`root_pages`, `pages_in_scope`), about 450 ms p95
    at 20,000 files measured beside other suites
    ([dev/debugging.md](../dev/debugging.md) "Performance budgets"). The
    other budgets of `test_search_scale.py` hold as planned.
15. A duplicated page's document is searched by the agent only, as before:
    the panel's and `GET /search`'s maps leave copies out.
16. A file's old version's rows go at once when the vault replaces its
    document (`purge_doc`); when the file crosses the keep limit
    (`set_doc`) they wait for the next `purge_unused`.
17. The line mark: a quote no line holds marks the number and says its
    text is gone; a line beyond the part shown of a file too large to show
    whole (past 16 MiB) says so in words of its own, not as a line past the
    end. Back to the same page scrolls to its line as a target with no
    mark. Only the live pane marks; a tab group's read pane never does.
18. The extractor: `read_lines` counts the rest of a file by its `\n`, or
    by `\r` in a read that holds no `\n` (a CR-only file), so a lone `\r`
    beside a `\n` past the window is not counted; a binary file is a
    `ValueError`, and its `Decoded` is empty text with `bad` False, the
    shape the code view answers (the fixture's node half compares `text`,
    `bad` and `binary`; `encoding` carries Python's codec names). A run's
    text is its lines' up to `PAGE_CHARS` plus the newlines between them.
    `locate` folds diacritics as unicode61 does and, with no token on any
    line, gives the run's first non-empty line. An outline's caption is cut
    to the entry's 80 characters, and the paragraph fill takes only the
    budget the language's own entries leave. `page_path` answers `""` for
    a page in no work folder, where feedback's `file_path` answered its
    title.
19. The index: `hits.build` answers its hits with `more` (the `next` of
    `/document-search`) and takes `fan_out`, the agent listing a PDF's
    pages itself. `store_docs` stores a document of more than `STORE_CHUNK`
    units alone, in chunks, so a book never holds the write lock; the job
    flushes its batch at 50 documents or `BATCH_UNITS` units, and its stop
    is `Job.progress` raising `Cancelled`, the batch stored first.
    `is_secret` reads the names of the pages that own the hash (a secret
    name winning), not the title, so a renamed page is caught; the metadata
    lookup passes the page's view to `ensure_indexed`, so a PDF at an
    address keeps `at: "url"`. The `first_folder` arm for
    `pages_in_folder` was measured slower and not taken.
20. The chat and the tools: `TextHead` holds the numbered lines alone, and
    the label, the outline block and the call to read on are written per
    turn, so one cached entry serves a plain chat and a chat with tools and
    the prompt stays stable; `text_head` reads a file once for its head and
    outline, keyed by hash, extension, language and budget, a missing file
    not cached, the outline's budget at most half the text's. A chip's page
    is found by hash and extension among root pages (`page_for_file`
    answers library pages only), so a work folder's file is named; a chip's
    format is its name's, a `.bin` never text. A thread reads `author
    (agent), date: "body"`, its source after its place; `read_page` brings
    a page's threads with its first window when the chat's context does
    not hold that page. On MCP a `line` beside a bare folder link is
    ignored, as `pdf_page` is there, and `line=9-9` is kept as written. A
    text file's size is given in bytes, other formats' rounded.
21. The client: `isSourceLink` states the pill's rule once and `linePlace`
    its words; the paste-as chooser takes a range too; a pill's own label
    may name lines (`l.`, `ll.`, `line`, `lines`); `coverage.js` walks
    merged spans, since a text entry's lines run to millions. The panel
    sends `exclude` only when the open page shows a PDF, keys a document
    hit by its page too (`docHitKey`), clears another filter's hits at once
    rather than holding them back, puts the indexing hint and Show more
    after both document groups, and asks again when an indexing job it
    waited on ends (App's `indexed`), while each answer counts fewer
    documents pending, since a linked file out of reach stays pending. The
    code view's `decodeText` applies the one rule to the head and to the
    whole; the mark is the accent's wash and a bar at the gutter, a range's
    first line scrolled to the top; "Line N marked." is for screen readers
    only; a press on an unmarked line dismisses the mark too. The task row
    reads "Indexing documents for search" and counts documents; the home
    box offers "Search inside notes, PDFs and files".
22. The browser group `text-search` types its query again until the Files
    hit shows, since the first search starts the index; its selection rules
    also name `chat/**`, `editor/**`, `ai_tools.py`, `mcp_server.py` and
    `work_folders/paths.py`, the last with `feedback`.
23. Not built in step 4: the Files window and server-side quick open (step
    5); `text_position` anchors and a selection of lines reaching the chat
    (step 6); eager indexing on a vault round; a per-format index budget for
    data-ish text (`csv` and `log` share the text caps);
    `view_image(page_id=…)` for a picture page (the context names no tool
    for one); a scoped search that does not read every file page per
    request (one query for the scope's documents, or a docs map kept by
    the workspace's change sequence), which item 14's budget waits on.

**As built (step 5).** Built as its row says
([dev/work_folders.md](../dev/work_folders.md) "The Files window", "Files
by name" and "Paths", [dev/home_library.md](../dev/home_library.md) "Quick
open" and "Work folders", [dev/synced_vaults.md](../dev/synced_vaults.md)
"Links and highlights", [dev/api.md](../dev/api.md) "Folders and labels",
[dev/ui-design.md](../dev/ui-design.md) "Keyboard focus" and "Dock
windows"). The Files window is a lazy chunk (`explorer/FilesWindow.jsx`,
`FileTree.jsx` over `fileTree.js`'s rows and keys), its state two explorer
hooks App calls (`useFilesWindow.js` before App's page lookups,
`useFilesTree.js` after them, with `useDirectoryRows.js` over
`directoryRows.js`), so App gained no counted hook; the dock dispatches
its fourth window through tables (`GRIP_GUIDE`, the fold-forget table).
Quick open's Files group is `GET /api/files` (`work_folders/file_search.py`
the match rule, over the partial index `idx_ub_file_name`), drawn by
`explorer/useFileSearch.js`; a path resolves through
`GET /api/folders/{id}/at` (`paths.page_at_href`), the client's twin of
the path rule is `explorer/workPaths.js`, and both are held to
`tests/shared/workpaths.json`. `POST /pages/summaries` names `+parent_id`
(its lookup of 40 ids about 1 ms where it took 11 at 20,000 files), and a
read-only hosted server lets it through. The browser group is `explorer`, its 20,000 files seeded
by `backend/tests/seed_work_folder.py` (the perf fixture's rows, run as a
script through the harness's `Server.python`). Where it differs from the
plan above, the whole list:

1. The Files window starts closed, and its showing is per browser, not per
   page: the home view is already the library, and the window earns its
   place beside an open page and stays open across pages once opened
   (`layoutKeeping`: a page's saved layout keeps it where it is, and its
   fold). View → Files and the command `app.toggleFiles` (unbound) bring
   it; its chunk is fetched with the startup requests when it is open,
   else on the View menu's hover.
2. The tree is a folder tree: labels have no place in it (a label view is
   the listing's flat mirror), and library pages filed in no folder are not
   in it. Folders and labels stay the model.
3. Files by name or path is one route, `GET /api/files`, over every work
   folder, `folder` optional for one work folder or a directory's subtree;
   quick open searches them all, as it searches every page.
4. The match rule lives on the server alone: the client draws the server's
   rows and marks the server's spans, so there is one rule and no shared
   fixture for it, and no typo pass for files. A directory is named with a
   slash: a query holding `/` or `\` is one path term, spaces kept
   (`old drafts/v1`); any other query's words match the file's name alone
   (`figures fig1` finds nothing, `figures/fig1` does).
5. An index on the file's name (`idx_ub_file_name`, over
   `COALESCE(file_name, content)`) is added with no migration step
   (`BLOCK_HOT_INDEXES`, made when a workspace opens); "no path is stored on
   a page" holds, paths are computed from the tree per request. A leaf
   named exactly is one seek of that index; otherwise `page_at_path` reads
   the directory's rows on `idx_ub_dir` and applies the name rule in
   Python.
6. Links by path: in a work folder's round a note's link to a tracked file
   that has a page becomes a mention of that page, which opens it, survives
   a move on disk and feeds backlinks; a picture's embed keeps its upload
   address, a paired note's link to its own PDF stays the file's chip, and
   a file new in the same round is its upload until the note is read
   again. A kept folder is unchanged. Gamma's mention of a file page, or of
   a block in its notes, is written back by the file's path and asks no
   anchor (`LinkContext._anchor` makes none for a page whose path is no
   note), rather than marking the page read-only, since a paired PDF
   shares its note's page. What the vault did not resolve (a path typed in
   Gamma, an untracked file, a folder that does not pull) resolves on a
   click through `GET /folders/{id}/at`, from the note's directory (known
   from its folders, whether or not its file is written yet), opened as a
   link jump Back returns from; a path naming none says "No tracked file
   at …" in the status pill.
7. The crumbs start at the work folder, the folder a person opens, as the
   home bar's do; a path (feedback, the tools, the files route) leaves it
   out. The directory's chip leaves the label field of a file's page.
   `page_path` now answers `""` for a page whose `folders[0]` is outside
   its work folder, as its twin does.
8. Quick open's Files group lists at most 20 rows, deduped against the page
   rows (a library file found by its title is one row, the page's).
9. Pins join the pickers' near set (tabs, recents and pins).
10. On a phone the Files window is an overlay beside an open page, reached
    from the More sheet's Files row or the command; on the home view the
    listing is the explorer and the row is not offered; there is no fifth
    bottom tab.
11. The reveal finds a row within the first 500 of a directory by name;
    past that the directory stays open with "Show more" and the row
    unrevealed.
12. A single click opens a file in the tree, and selects it, where the home
    list view selects on a click and opens on a double-click; the tree's
    selection is its own, and a file row's menu is the page menu for that
    one page with the home selection let go (`openSubPageMenu`), so F2 and
    Delete never act on what the tree selected.
13. The reveal and the directory reads happen only while the window is
    shown; a file opened with the window closed reads nothing of the tree,
    and the window catches up with it when it is shown. An open directory
    is read again only when the listing's count of its files changed or the
    listing's `work_changes` name a write to one of its files since.
14. The scale budgets, measured at 20,000 files and set at about twice
    their p95 ([dev/debugging.md](../dev/debugging.md) "Performance
    budgets"): `GET /api/files` 25 ms for a name, 22 for a path term, 16 for
    no hit, 95 for a query with no ASCII run (every name read and ranked);
    `GET /folders/{id}/at` 11 ms; the summaries' lookup of 40 ids 2 ms on
    the connection (through the test client the route takes about 9 ms, its
    40 previews and the request itself, which the 5 ms ceiling could not
    hold). The quick-open cases run before the case that adds 2,000
    directories to the fixture, so they time the 11 directories of the
    seed; the path case resolves `big/file-01234.bin`, where the seed puts
    that file.
15. The seed's and the perf fixture's files are listed binaries
    (`file-NNNNN.bin`, `at: "listed"`, no hash) and notes: a PDF is never
    listed, and a listed document reaches no indexer and no reader of
    bytes, so a seed inside a live server costs nothing while quick open is
    timed.
16. The browser group runs quick open first, before any file is a tab or a
    recent (those are page rows, which the Files group leaves out), and its
    path query is `dir 3/012`: a leaf held by names in every directory, so
    the segment does the choosing. Its phone step looks up `fig2`, the one
    figure no earlier step opened; it pauses the work folder before it
    types paths into a note made in Gamma, so no round names the note or
    rewrites the links; and it allows the one 404 of `/folders/{id}/at`
    its unresolved link asks for. `select.mjs` gives the new client files
    their own rows (`fileSearch.js` and `useFileSearch.js` with quick
    open's groups, `workPaths.js` with `notes-pdf-share` and `sub-pages`
    too, since every note link asks it), and `routers/folders.py` names
    `work-folders` beside `explorer`.
17. The reads: the directory's path is `paths.dir_path`, as step 4 named it,
    and the predicate of a relative link is `paths.is_relative_href`, the
    server's half of the fixture's `hrefs`. A leaf named exactly is one
    seek of `idx_ub_file_name`, so a stored name with spaces around it is
    no longer an exact twin where another file names the leaf exactly.
    `GET /folders/{id}/at` answers a link that names nothing (a scheme, a
    leading `/`, `#` or `?`) with the 404 of a path that names no file.
    The rank folds each name once and drops a row at the first term its
    name lacks, which holds the no-run case at 48 ms p95.
18. The vault: a page mentioned by a note's link is spelled back by its
    path, and a paired paper's page by its note's (`page_path` keeps the
    note's path; the label is the leaf only for a path that is no note),
    so a mention of a work folder's PDF made in Gamma is written as
    `[[p.pdf]]`, where it was the upload's address. A file link's
    `#fragment` (`[[main.pdf#page=3]]`) names nothing in Gamma's text and
    is dropped there; the remembered spelling keeps it on disk. A file
    moved on disk inside a round keeps its old path in that round's table,
    resolving a stale link to the same page.
19. The tree: the hidden flag and the open folders are kept in
    `localStorage` by `useFilesWindow.js` itself, not through
    `usePersistedFlag` / `usePersistedState` (the hook is tested under node,
    which `shared/lib/utils.js` does not load, and the open folders' key
    changes with the account, where `usePersistedState` reads its key
    once). `revealPath` lives in `workPaths.js`, which App imports, so
    nothing of `fileTree.js` reaches App's startup. `directoryRows.js`
    holds `NONE`, the page sizes and the reveal's paging (`findStep`);
    `useDirectoryRows` adds `retry`. Only directories whose every folder
    above is open are read. A "Show {n} more files" row counts what one
    press adds (at most 200), a library folder's too, paged on the client;
    it and a note row are tree items, so Enter reads more or reads again.
    The keys leave Alt, Ctrl and Meta chords alone (Alt+← goes back).
    App's `patchKnown` and `dropKnown` reach the tree's rows, so a rename
    or a delete shows there at once. The window, quick open's Files group
    and the files' read need a signed-in member (`filesOn`); a share view
    has neither.
20. Quick open sends the query trimmed; the server's `parse` strips it
    too, so the answer is the same and a trailing space asks nothing
    again. A query the title matcher cannot read still lists the file rows
    after the page rows. The placeholder names files in a share view too.
21. The note editor's path links say "No tracked file at …" with the
    address percent-decoded (`my fig.pdf`); `resolve` gets it as written.
    `BlockTree` asks `isRelativeHref` itself, after its upload and Gamma
    link cases, so `PathLink` draws only relative addresses.
22. Not built in step 5: a filter box or search in the Files window (quick
    open is the search); typeahead in the tree; Copy path or Show in folder
    for a directory; crumbs in a tab group's pane; a path link to a
    directory (a trailing `/`); hover cards for path links; a `[[` or `@`
    picker over files (`useFileSearch` is ready for it); `in:files` in
    quick open (the search panel has it); a prefilter by directory for a
    path term with no leaf or a query with no ASCII run (both read every
    name, about 50 ms at 20,000 files); a read around an id for the reveal
    past 500 rows.

**As built (step 6).** Built as its row says
([dev/api.md](../dev/api.md) "The highlight shape" and the
`PUT /pages/{page_id}/attachment` row,
[dev/highlight_relocation.md](../dev/highlight_relocation.md) "Text files",
[dev/feedback.md](../dev/feedback.md) "The anchor", "An entry" and "The
Markdown", [dev/documents.md](../dev/documents.md) "Viewers",
[dev/comments.md](../dev/comments.md) "Threads" and "The margin",
[dev/ai_context.md](../dev/ai_context.md): a selection of lines, a
range's words). A highlight in a
text file is a block with `text_position`: `{line, col, end, end_col}`
(lines 1-based, the end inclusive; columns 0-based code points of the
decoded line), `prefix` and `suffix` (40 code points either side) and
`hash`, the version it was measured on, always written; its text is the
block's `quote`, as a PDF highlight's is. The quote and context of a span
are `highlights.span_text` on the server and
`documents/viewers/textAnchors.js` `contextOf` on the client, held to one
another by `tests/shared/textposition.json`. When the page's stored file
changes, by a work folder's round or by `PUT /attachment`,
`gamma/documents/text_anchors.py` places each range again from its own
quote and context, with no old file: exactly near its old line, the lines
compared under a fold of whitespace (NFC, case kept), then word by word at
or above `relocateMinMatch`, else lost and kept with its best place under
the threshold as `closest`. The flags, the thresholds, the words and the
compare-and-set write are the PDF run's (`highlight_relocate._write(…,
key=)`). A file of at most 512 KiB is placed in the replacement's own
request, a larger one by the `relocate-highlights` job. A thread on a range
is a `lines` anchor, and its feedback entry names the file's own lines
(`where.line`, `end`, `was`; `source` the file). The code view draws the
ranges over their text, a selection's popup makes a highlight or a thread,
the cards stand in a column beside the text, a phone shows badges, and a
selection reaches the chat by its lines. No migration: `text_position` was
the kind column's since step 55. The browser group is `text-anchors`.
Where it differs from the plan above, the whole list:

1. The run reads no old file. The plan says "the rule set for PDFs", whose
   run reads its reference from the stored old version; a text range
   carries its `prefix` and `suffix` (the W3C `TextQuoteSelector`'s idea),
   so the ladder needs only the new text. The two runs share the flags,
   the thresholds, the write, the words and the client's looks, not the
   reading. [highlight-anchoring.md](highlight-anchoring.md)'s open item on
   storing the context is closed for text.
2. No version is kept for a text file's thread (`kept.KEPT_FORMATS =
   ("pdf", "image")`): the plan's "the version a comment was made on is
   kept" serves the PDF run and the pictures, and text needs neither.
   `held_file` is `""` for a `lines` anchor, and a text page under an open
   thread stores none of its rebuilds, so the run reads the linked path. A
   `file` or `note` anchor on a text page still names `<hash>.tex`, which is
   never stored for it ([dev/feedback.md](../dev/feedback.md) "Known
   gaps"). A page without a document keeps no bytes, whatever its
   threads.
3. `hash`, not `doc_id`, names the version a range was measured on, always
   written and never held, as a region's is: a replacement stamps nothing
   on text ranges, and "pending" is read (`hash` not the page's file, and
   neither `lost` nor `approx`).
4. Columns are code points, which the plan left without a unit. A span
   never ends in a line break: an end at column 0 of a later line is moved
   back to the end of the line before, again past empty lines; the quote is
   cut at 10,000 code points.
5. The run is in the request up to `TEXT_INLINE_BYTES` (512 KiB), the job
   above it, where the plan has one job: a formatter run over a repository
   would meet the 20 jobs an account may run, and the pending window goes.
   The cost sits on the vault's round: fifty files of 1,000 lines with ten
   ranges each, placed in their rounds, take about 1.3 s against a budget
   of 1.5 s, and the ladder alone places 100 ranges in a 50,000-line file
   in about 0.5 s against 1.0 ([dev/debugging.md](../dev/debugging.md)
   "Performance budgets"). The choice reads the new document's `size` (a
   document without one is placed in the request). Its counts go back as
   `relocation` on `PUT /attachment`'s answer, which the replacement's
   status reads ("File replaced." and `relocationSummary`); the vault's
   path logs them under `[relocate]`. A refusal of the run is answered as
   `relocation_error`, any other failure logged and answered as "the
   highlights could not be placed: …", since the replacement is committed
   and the vault's round must not fail on it. The job records `format` in
   its params, a PDF run's too.
6. The run hashes the bytes it read and refuses ("the file changed while
   it was read") when they are not the version it was asked to place; the
   ranges stay pending until the next change. A PDF run reads a stored
   file, whose bytes cannot change under it.
7. The fold: whitespace runs are one space and the ends trimmed, NFC is
   applied a character and its marks at a time (equal to NFC except across
   two base characters, Hangul's conjoining jamo), no case folding: a
   re-indentation or a CRLF rewrite leaves a range exact, a renamed
   identifier does not. The exact step treats a quote's first and last
   lines as parts of lines unless the selection took them whole, and
   bounds itself (the 500 copies nearest the old line ranked, 32 lines
   either side of the old line per seed word); a solid placement takes the
   new file's context, a dashed one keeps the stored one. A pending range
   found nowhere goes back to its reference (`prev`'s lines, which store no
   columns); a lost range whose version came back with its quote standing
   is written bare and counted kept.
8. No review card for text (the PDF's hangs on rectangles): a dashed range
   is accepted from its menu or by the Document menu's Accept all; a lost
   one is placed by hand through "Move to a selection…" or "Use the closest
   match ({n}%)", the review card's step as a menu row, shown for a lost
   text range that has a `closest`. Accept and the closest match are keyed
   by the block's own position (`placeKeyOf`), where they read
   `pdf_position` before; `closestPlacement` takes the block's quote too,
   since a text position stores none and its `prev` needs it. The Document
   menu's Review row stays the PDF's; "Find highlights in this file" runs
   the job by hand, and its route answers a page of another format "the
   page has no PDF or text file" (it said "no PDF"), a text page without
   ranges "the page has no highlights placed on its file" and a listed one
   "the file is listed without its bytes".
9. A lost range is drawn as a dashed tag at the start of its old first
   line, not as a hollow mark over its old lines: for text the old line
   number holds other text now. The line cue stays on its old lines, and
   its card stands at that line.
10. No `estimate` and no `earlier` for a text range's `placed`: `exact`,
    `rough`, `lost` and `pending` serve.
11. The viewer surface keeps step 3's names `onRegionJump`,
    `onRegionContext` and `onRegionThread` for a range, and gains
    `onAnchor`, `onChatSelection` and `onHighlightAction` (the bar over a
    range the highlighter just made). The viewer flag was not renamed:
    App reads `regions` as "a region drawn with a finger", and the margin
    flag already existed as `margin`, which `text` now carries beside
    `tools: ["highlighter"]`. One gate in App, `takesHighlights` (the
    viewer's tools name the highlighter; a PDF needs its address too),
    serves the highlighters, their strip, the command and the notes' Move
    button; `highlight.created` names a range's kind `lines`.
12. A selection reaching the chat is a labelled item (`line`, `end`,
    `file`; the chip "Line 33 · main.tex"), not a new path: the page-0 path
    existed. App's own selection readers skip the code view, a plain click
    there clears the set, and a click on a range adds its quote with the
    file's name. On the server a selection with lines is headed `lines
    118–120` and read with 20 lines either side through `text_window`, the
    same budget as the PDF's windows; a cut one carries its `lines`, so the
    shortened pill names it "Lines 118–120" where it said "PDF passage".
13. `TipFrame` is lifted to `shared/ui/TipFrame.jsx`, its layer the
    `--z-tip` token; the code view has its own small `TextTip` (the marks,
    Copy where the host's menu is off, Comment on this passage), and no
    Link, Translate or Move; the highlight menu leaves the Link rows out
    for a text range alone. The selection takes a highlighter's tint only
    while one is armed, and a linked range is drawn in the accent.
14. The column's tops come from the viewer (`CommentMarginSlot`'s
    `topOf`), not from rectangles. It stands beside CodeMirror's scroller,
    moved with its vertical scroll alone, when the view has room for
    360 px of text beside it, never on a phone; with Wrap off a long line
    scrolled sideways passes under its gap. Its tops and a phone's badges
    are read in CodeMirror's measure phase, the badges from the drawn
    marks, so a badge exists only while its line is drawn, and a press on
    one jumps to its range and opens its threads (the PDF's and the
    picture's badges do), where the plan had it open the bar over the range. A jump to a range not yet loaded waits
    until the whole file is read, and scrolls only when the range is out
    of view. comments.md's "The PDF margin" is "The margin".
15. The anchor's `lines` key is written on `lines` anchors alone, so the
    other kinds' key order stands. `place_words` names one as `lines
    118–120, “quote…”`. The chat's thread lines, the feedback tools'
    answers (`anchors.following`), the entries and the agent's prompt name
    the lines the block holds now, not those the anchor was opened on. A
    malformed `text_position` places nothing, so a thread on it reads as a
    note.
16. One vocabulary for the place words, client and Markdown, every format:
    "moved in the new version", "not found in the current version", "drawn
    on an earlier version", where the PDF's said "build".
17. A rough entry carries `was`, the quote the comment was made on (from
    `prev`): the text twin of a PDF entry's `before` picture. The local
    agents' prompt carries it too. An entry's order puts a page's notes
    after its placed threads, and a text file's threads by line.
18. The AI's words for a text range's highlight are its lines and state
    (`line 52`, `line 47; not found in the current file`, `line 23; placed
    roughly`), after the quote of the page section's `- Highlighted:` line
    and of `read_block`'s, so the model reads on with
    `read_page(from_line=)`.
19. The client's model: a block with a `pdf_position` and a
    `text_position` is placed by the PDF's (`placeKeyOf`, as the server's
    `_placed`), and a kind is read region, then range, then PDF. A range's
    Properties row reads "Lines 118–120" with its chips (To check, Lost,
    "Measured on an older version of the file"), which the status line
    shows too. A range is drawn pending only when both its `hash` and the
    page's file are known and differ; a span ending at the start of a cut
    line stops before it, and a line break alone makes none.
20. The browser group makes ten of its threads through the API on whole
    lines (a highlight and a thread come from selections in the view). The
    replaced last sentence keeps a closest match above `CLOSEST_MIN`, so
    the group takes it from the menu and accepts it, where the plan
    expected none; the deleted paragraph's has none. Stacked cards are not
    level with their lines, so only the first card's place is measured.
    `select.mjs` names `text-anchors` beside the viewers, the comments, the
    editor, the chat, the feedback reader, the vault, `documents/text.py`
    and `highlight_relocate.py`.
21. Not built in step 6: placing a range whose `hash` never was the page's
    file before the next change (it waits for it); a real long press in the
    browser group (the phone step sets its selection by script); a review
    card for text; Link and Translate on a text selection; a format-aware
    hold for a `file` or `note` anchor on a text page; a check on a touch
    device of the code view's selection and of the column keeping pace
    with the scroll; a measured cost of the inline run for a quote of
    thousands of characters over hundreds of lines (the perf cases place
    two-line quotes).

## Still to decide

1. **Add to library keeps one page**: decided in step 1, as recommended.
   The file's page becomes a paper and stays at its path; Save a copy to
   library makes the second page, annotated apart, for a version kept.
2. **Every open thread on a work-folder file is feedback**: decided in
   step 2, as recommended, with no mark that sets an instruction to the
   agent apart from a remark to oneself. A highlight's own note stays the
   person's.
3. **The feedback files live in `.gamma/feedback/`**: decided in step 2,
   as recommended: hidden, beside the marker, out of the way of the
   repository, with a `.gitignore` of their own.
4. **The commented version of a PDF is stored** until its comments are
   resolved: decided in step 2, as recommended, every build shown while a
   thread is open (As built, item 4); it is what lets a comment on a figure
   survive a rebuild.
5. **What the dialog ticks to start with**: decided in step 1 (As built,
   item 8): PDFs and notes outside the directories git ignores, with the
   format filters one click away.
6. **Markdown in a repository.** A `README.md` ticked becomes a note
   through the lens, read-only in Gamma while the vault does not pull.
   Whether a file the lens cannot take should open in the code view
   instead of being reported is open.

## Risks

- **The vault engine.** Its round is where a file can be lost, and the
  registry touches most of `Round`. The refactor lands alone, against the
  existing tests; the new class comes after it.
- **The document's migration.** It rewrites every page that carries a
  file and pauses clones until both sides run it. The runner copies the
  databases first, and the step is tested against a backup of each older
  shape the normalizers still accept.
- **A reader missed.** A root-page reader that is not filtered sees a
  work folder's files: a lookup job or a link treating a figure as a
  paper. The classification is a list in the dev doc, and a test with a
  populated work folder runs the library-wide jobs.
- **`.gammaignore` written wrong.** Git's rules have corners (a file
  cannot be taken back from under an excluded directory). The dialog
  writes only forms it reads back to the same selection, and says how
  many files a saved selection drops before it drops them.
- **A token that can answer.** A reply and a resolve written by an agent
  are writes to the workspace. The MCP scope does those two things and no
  other, and the token's name is on every comment it makes.
- **App.jsx.** The viewer seam and the listing are in the file every open
  branch edits. `DocumentPane` should land on `dev` early and small.
- **Windows.** Gamma holds a file read in place open while it serves it,
  which can make a build's replace fail and retry. The existing limit
  stands; storing the commented version reads the file once more at the
  moment a thread is opened.

## Relation to earlier notes

- [dev/block_centric.md](../dev/block_centric.md) says there are no page
  types and that pages are described by what they carry. This plan keeps
  that: a format belongs to the document (`doc.format`), never to the
  page. It
  reverses "no page-tree sidebar". The viewer is not rewritten, as that
  doc asks; it becomes one of several.
- [vaults.md](vaults.md) left "files that are neither notes nor PDFs nor
  pictures" as not listed at first, and did not propose files as the truth
  for ordinary folders. The first is taken up here; the second still
  holds. Its rule that a page filed below a vault's folder is a file in
  its directory becomes the stricter "one place" above, and its vault's
  pages stop being library pages by default.
- [comments-inbox.md](comments-inbox.md) left comments out of the AI's
  reads, search and MCP. The feedback reader is that reader.
- [latex-projects.md](latex-projects.md) put a project's files in a page
  as blocks. For a directory on disk the work folder replaces that shape;
  its build job, SyncTeX and editor parts apply to a work folder's files
  unchanged.
- [data-model.md](data-model.md) considered a page-type column and found
  nothing that needs one. That holds: `work_folder` says where a page is
  listed, not what it is.
