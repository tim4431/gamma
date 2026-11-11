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

Status: **a plan; step 0 is built** ("As built (step 0)" under "In
steps"), the rest is not. This note surveys what the stored model and the
code assumed before step 0, weighs three shapes a file could take, and
lays the chosen one out in layers from the bottom, with the steps, the
decisions still open and the risks. It was revised on 2026-11-11 after
the owner's answers ("Decided" below). The mechanics as built live in
[dev/documents.md](../dev/documents.md) (a page's document),
[dev/block_centric.md](../dev/block_centric.md),
[dev/user_db.md](../dev/user_db.md), [dev/home_library.md](../dev/home_library.md),
[dev/synced_vaults.md](../dev/synced_vaults.md) and
[dev/comments.md](../dev/comments.md).

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
  is a view of them.
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
were, marked as made on another version.

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
| Later | Work folders of another server from the dialog; editing a text file in Gamma; notebook and table viewers; building LaTeX ([latex-projects.md](latex-projects.md), parts 4 and 5, on a work folder's files). | |

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

## Still to decide

1. **Add to library keeps one page** (recommended): the file's page
   becomes a paper and stays at its path. The other reading is a second,
   library page that follows the file, which gives one file two sets of
   comments.
2. **Every open thread on a work-folder file is feedback** (recommended),
   with no mark that sets an instruction to the agent apart from a
   remark to oneself. A highlight's own note stays the person's.
3. **The feedback files live in `.gamma/feedback/`** (recommended):
   hidden, beside the marker, out of the way of the repository. A visible
   directory is the other choice, if agents should find it unprompted.
4. **The commented version of a PDF is stored** until its comments are
   resolved (recommended). It costs a copy of the PDF per round of
   comments, and is what lets a comment on a figure survive a rebuild.
5. **What the dialog ticks to start with**: PDFs and notes (recommended),
   with the format filters one click away.
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
