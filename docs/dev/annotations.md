# Annotations: highlights and comment threads for an agent

A person's highlights and comment threads are what an agent reads back: a
comment is a request about the page or file it is on, a highlight what they
marked, with the note they wrote on it. Nothing new is stored for either:
a thread is open until someone resolves it, a reply says what was done, and
reopening sends it back ([comments.md](comments.md)). What this adds is
what makes them usable from outside the page: the place a thread was
opened on, kept with it; a reader that turns the threads and the bare
highlights (those no thread is on) of a page, a folder or the library into
entries with their file, place, source line, picture and comments, served
as JSON and Markdown over HTTP, over MCP and the chat's tools, and for a
work folder or a followed file as files on the disk; the version of the file (a build's
PDF, a figure) a thread was opened on, kept while the thread is open; and a
token scope that answers threads and can do nothing else. A person asks an
agent by sending it a comment (the composer's Send to tick): that is a
turn of the thread's own conversation, [local_agents.md](local_agents.md)
"A comment sent to an agent". Backend: the package `gamma/annotations/` and
`gamma/routers/annotations.py`. The plan was
[research/file-explorer.md](../research/file-explorer.md) section 7 (step 2
of "In steps"; step 3 adds a picture's regions and the whole picture); the
reader of any page is step 3.1 of
[research/agents-as-participants-plan.md](../research/agents-as-participants-plan.md),
whose step 3.7 removed Send's requests, the request file and the Feedback
dialog (migration step 65 drops their tables); the surveys behind the
SyncTeX reader and the pictures are research notes.

| Module | What |
|---|---|
| `anchors.py` | the place a thread was opened on, kept on its root ("The anchor") |
| `entries.py` | the reader: scopes, entries and the document ("The entry and the document") |
| `markdown.py` | the Markdown form ("The Markdown") |
| `pictures.py` | the region pictures and their cache ("The pictures") |
| `synctex.py`, `sources.py` | the SyncTeX reader, and a place on a work folder's PDF mapped to its source ("SyncTeX") |
| `kept.py` | the commented version kept ("The commented version kept") |
| `files.py` | the files below a work folder's `.gamma/annotations/` and beside a followed file ("The files") |
| `listeners.py` | the hooks on comment writes and op commits ("What keeps it current") |
| `hand.py`, `places.py` | the agent's hand: what the writing tools write as the agent, and the places they find ("The writing tools") |

The words: **W** is a work folder's root folder
([work_folders.md](work_folders.md)); **a file** a page with
`properties.work_folder` (a tracked file, a page at its path); **a library
page** and **a library folder** one outside every work folder; **the
library** every live page, `home` as the chat's buckets name it; **a
thread** a root comment and its replies; **a bare highlight** a highlight
block (the `kind` column's `highlight`: a passage or area of a PDF, a
region of a picture, a range of a text file; never a link region or ink)
that no thread is on; **an agent** whatever writes through an
integration token; **the person** the account the token belongs to;
**open** a root neither resolved nor orphaned, a tombstone root that still
has replies included (`comments.open_counts`'s rule, [comments.md](comments.md)
"Threads"), so the list view's count, the entries, the hold on the
commented version and `comments.has_open_thread` agree.

Every page has annotations, a library page as a work folder's file, and a
page a vault of one file follows has its files on the disk as a work folder
has ("The files"); what stays a work folder's alone is the repository's
side: SyncTeX's source lines. An agent answers and asks on any
page with the writing tools ("The writing tools"). Threads by other members, and by
signed-in accounts a folder share admits, are entries too: the entries say
who wrote each comment (`by`) and the document whose account it is written
for (`owner`), and the agent's skill acts on the owner's comments and asks
about anyone else's ([mcp.md](mcp.md) "The Comment level").

## The anchor

A root comment keeps where its thread was opened, `comments.anchor`, JSON
(`gamma/annotations/anchors.py`; the column is [comments.md](comments.md)
"Threads"):

```text
{"v": 1,
 "kind":   "passage" | "area" | "ink" | "region" | "lines" | "note" | "file",
 "block":  "<the block the place is read from>",
 "doc_id": "<24 hex>" | "",          # the file the place was measured on
 "ext":    ".pdf" | ".png" | …,      # the page's document's extension when the thread opened; "" for none
 "page":   3 | null,                 # the 1-based PDF page; null for a region of a picture and a range
 "rects":  [[x1, y1, x2, y2], …],    # fractions of the page (or the picture) as shown, top-left origin, 4 dp, at most 200
 "quote":  "…",                      # at most 1000 characters; "" for an area, ink, a region, a note, the file
 "box":    [x0, y0, x1, y1] | null,  # the region picture's crop, fractions, 4 dp
 "lines":  [118, 120],               # a range's alone: its first and last line when the thread opened
 "source": {…}}                      # added after the commit by the kept worker; absent until then
```

`anchors.anchor_of(conn, page_id, block_id)` computes it inside
`comments.create`'s transaction, from the rows alone (no file is read):

- **file**: the thread is on the page itself (`block_id == page_id`);
  `doc_id` is the page's document's hash, no page.
- **passage** or **area**: the block is a highlight or a link region with a
  `pdf_position` (`blocks_store.block_kind` `highlight` or `link`): `area`
  when the position says so or the quote is empty, else `passage`. The rects
  and the quote are `highlights.previous_place`'s (the stamp's shape). `doc_id`
  is the position's own when it carries one (a highlight not yet placed in
  a replacement: its rects are the old file's), else the page's current
  file.
- **ink**: an ink group with a `pdf_position` (a group on a PDF page, not on
  a sheet); `rects` is its bounding rectangle as fractions.
- **region**: a highlight or link block with an `image_position`, a region
  of the picture the page shows ([documents.md](documents.md); a block with
  both positions is on the PDF page). `doc_id` is the version it was drawn
  on (`image_position.hash`, else the page's), `page` null, `rects` its
  one rectangle as fractions of that version (`highlights.region_box`),
  `quote` "".
- **lines**: a highlight or link block with a `text_position` whose span
  reads (`highlights.text_span`), a range of a text file
  ([api.md](api.md) "The highlight shape"; a block with a PDF or picture
  position too is placed by that one). `doc_id` is the version its lines
  were measured on (`text_position.hash`, else the page's), `page` null, no
  `rects` and no `box`, `quote` the block's, and `lines` its `[line, end]`:
  a key only this kind carries, so the other kinds' keys and their order
  stand. A malformed `text_position` places nothing (the block is a note).
- **note**: anything else. The nearest ancestor below the page that is a
  highlight, a link region, an ink group, a region or a range with a position gives
  the place: `kind` becomes that ancestor's and `block` its id (the
  annotated export's rule for a note under a highlight). Without one the
  thread is a `note` on the block itself, with the page's `doc_id` and no
  page. Text boxes, pictures, sheets and recordings are notes.
- **ext**: the page's document's extension when the thread opened, so
  `doc_id` and `ext` together name the stored file the place was measured
  on (`held_file`); "" for a page without a document.
- **box**, the picture's crop (the module's constants): an area's or a
  region's rectangle padded by `AREA_PAD`; a passage's rectangles' union
  grown to at least `PASSAGE_MIN_W × PASSAGE_MIN_H` of the page and padded
  by `PASSAGE_PAD` (the chat's selection crop, `ai_context._crop_box`); ink
  by `ink_view.grow_box`, the rule `view_ink`'s crop uses, so the two never
  differ; all clamped to the page; null for a note and the file. A position
  with no frame (a Logseq import) has no rects and no box.

The anchor never changes with the block: it is the place as commented,
which the reader compares with the block now. A reply holds none. Nothing
is backfilled for a root from before migration step 54
([migrations.md](migrations.md)): its place is read from its block. The
module's pure helpers: `parse(text)` (a stored anchor as an object, None
for NULL or a malformed one, a `lines` anchor without two whole lines
included; a dict passes through), `held_file(text)`
(`stored_name(doc_id, ext)`, the stem through `storage.stored_pdf_stem`,
"" for none: the hold, "The commented version kept"; an anchor written
before step 3 has no `ext` and holds a PDF, one whose `ext` is no
extension holds nothing, nor does a `lines` anchor), `top(anchor)` (the
place's top: the highest rectangle's, else the box's, else 0: the order of
a page's threads) and `place_words(anchor)`, the place in words the
writing tools' answers and the chat's thread lines use: `the whole file`,
`a region of the picture`, `page 3, “…”`, `page 5, an area`, `page 2,
handwriting`, `lines 118–120, “…”` / `line 118` (`line_words`), `a note`.
A range's anchor keeps the lines it was opened on; `following(conn,
page_id, anchor)` gives the lines its block holds now while it is still a
range, which both of those name (the entry and the agent's prompt read
the block's lines too).

## The entry and the document

`gamma/annotations/entries.py` reads; it writes nothing and renders nothing.

### Scopes

```python
class Scope(NamedTuple):
    kind: str            # "work_folder" | "folder" | "file" | "page" | "library_folder" | "home"
    id: str              # W, a folder below W, a page's id, a library folder's id, or "home"
    work_folder: str     # W; "" for a library page, a library folder and the library
    name: str            # W's name, the directory's path in W, the file's path, the page's title,
                         # the folder's path ("Physics / QEC"), or "" for the library
```

`resolve_scope(conn, scope_id, tree=None)`: `home` (`entries.HOME`) is the
library; a folder id is W (`work_folder`), a folder below one (`folder`,
named by its path in W) or a library folder (`library_folder`, named by
its path from the top, `blocks_store.PATH_SEP` between the names); a live
root page with `work_folder` is a `file`, any other live root page a
`page`, named by its title. `AnnotationError(404)` for no such folder or
live page (a page in Recently deleted is none). `work_scope(conn, scope_id,
tree=None)` is `resolve_scope` for what is a work folder's alone (a far
turn's file, the files' writer, the chat strip's path):
`AnnotationError(400, "That is no file of a synced folder.")` for a
library page or folder and the library (`WORK_KINDS`).
`work_folder_named(conn, name_or_id, tree=None)`: a W by
its id, or by its name when exactly one carries it (404 for none, 400 for
two: name one by its id), by the work folder name rule of
`work_folders.paths.work_folders_named`: exactly, else ignoring case with
`lower()` ([work_folders.md](work_folders.md#paths)). `AnnotationError(status, detail)`
lives here; the routes answer it as an `HTTPException`, the MCP tools as an
error text.

### Which threads and highlights

A scope's pages come first, each read once (`_page_ids`, `_pages`): a
`file` or a `page` alone; W's files by the pages' `work_folder` column
(`idx_ub_work_folder`), a `folder` scope those whose `folders[0]` is in
`marks.Tree.subtree(scope.id)`; a library folder's pages,
`blocks_store.pages_in_folder` (filed in it or below, a sub-page where its
top is, a work folder's files below it among them, as folders are labels:
[home_library.md](home_library.md)); the library's, every live page
(`blocks_store.live_pages`). A page in Recently deleted is none, and a work
folder's scope keeps the pages whose work folder it still is. Their threads
are `comments.threads_for` (an orphaned one is none). `state` is `open`
(the default), `resolved` or `all` (`AnnotationError(400)` otherwise), and
filters the threads alone: a bare highlight is listed whatever the state.
A highlight is bare when no thread of any state is placed on it, its own
block or a block below it (the thread's anchor's `block`, a root from
before step 54 read from its block): such a highlight is read by its
thread's id. A highlight block whose position does not read (no place) is
no entry.

### A file's name

`file_path(tree, props, paths=None, *, page_id, title)`: `paths[page_id]`
when given, else the tree's path, `work_folders.paths.page_path`
([work_folders.md](work_folders.md#paths)): the names of the folders from
the page's `folders[0]` up to W (W left out) joined with `/`, then its
`file_name`; a page without a `file_name` (a note page) is named by its
title, and so is a library page ("Untitled" without one). The disk's override is the files' alone; the chat's tools and the
search name a file by `page_path`. `paths` is `{page id:
path}` from the vault's rows on this computer (`paths_of(vault.store.files(link_id))`:
each page at its PDF row's path, else its notes row's; tombstones, a row
with `was` or an in-place one, left out), what the files' writer passes so
the names are the disk's. Separators are `/`.

### An entry

```text
{"id":       "<thread root id>" | "<the highlight's block id>",
 "type":     "thread" | "highlight",
 "file":     "main.pdf",                   # a library page's title
 "format":   "pdf",                        # the document's format; "markdown" for a note page without one
 "page_id":  "…",
 "block_id": "…",                          # the block the thread hangs on, or the highlight
 "kind":     "passage" | "area" | "ink" | "region" | "lines" | "note" | "file",
 "where":    {"page": 3, "quote": "…", "box": […], "rect": [x1, y1, x2, y2], "size": [w, h],
              "placed": "exact", "match": 0.82, "note": "…"},
             # a range: {"line": 118, "end": 120, "quote": "…", "placed": "rough", "match": 0.86, "was": "…", "note": "…"}
 "source":   {"file": "sections/method.tex", "line": 118, "end": 120, "version": "current"} | null,
 "picture":  "img/<key>.jpg" | null,
 "before":   "img/<key>.jpg" | null,
 "state":    "open" | "resolved" | null,   # null for a highlight
 "link":     "<the entry's Gamma link>",
 "comments": [{"id", "by", "agent", "at", "body", "deleted", "agent_kind"?}]}   # [] for a highlight
```

The keys come in that order, and a `where` key with no value is left out.
A bare highlight's entry is a thread's without the thread: its place is
read from its block as a root without an anchor is, `where.note` is the
text written on the highlight, `state` is null, `comments` is
empty, there is no `before` (nothing kept it) and its link has no
`thread`.

- **`kind` and `where`** come from the anchor when the root has one, read
  against the block now, else from the block alone (the same
  `anchors.anchor_of` rules, live). When the block the anchor names is gone,
  or is no longer a place (a note moved out from under its passage, the
  passage deleted), the entry keeps the anchor's place. In `where`:
  - `page`: a passage, an area, ink, and a note under one;
  - `quote`: a passage's, the highlight's text now when the block is there,
    else the anchor's;
  - `box`: an area's, ink's or a region's. Their `page`, `box` and
    rectangles are the anchor's place while the file is still the anchor's
    (a block dragged on the same build does not move the thread: its
    picture and its source line are read at that place too), else the
    block's position now as fractions (the relocation on a new build). A
    passage's follow its highlight; a region's are the anchor's always
    (nothing places a region again);
  - `rect` and `size`, a region's: its rectangle and its picture's size in
    the picture's own pixels, whole (`image_position` as drawn, the version
    `image_position.hash` names); left out when the block is gone;
  - `note`: the text of the block the thread hangs on (at most `MAX_NOTE`,
    1000 characters) when it is a note, a text box, a picture's caption or a
    note under a place; a bare highlight's own text;
  - `placed`, for every kind with a page: `exact` (measured on the page's
    current file, neither approximate nor lost), `rough` (placed again
    approximately with a `match` score, which `where.match` carries),
    `estimate` (approximate without one), `lost` (the run found it nowhere),
    `pending` (the position carries another file's `doc_id`: measured on an
    earlier build, its run not done, whatever else it carries — an area the
    run could not place keeps its rectangles there, marked `approx`),
    `earlier` (an ink anchor whose file is
    not the page's current one: ink is never placed again, so an ink thread
    on a later build leads with its `before` picture). The vocabulary is
    [highlight_relocation.md](highlight_relocation.md)'s. For a region:
    `exact` when the version it was drawn on is the page's current picture,
    `earlier` when another (it stays where it was; the viewer draws it
    dashed), `lost` when the page's document is no longer a picture.
    Nothing for the file.
  - a range of a text file (`lines`, and a note under one) follows its
    block, as a passage does: `line` and `end` (left out when equal to
    `line`) are the block's lines now while it is placed on the page's
    current file (`placed` `exact` or `rough`: the run's result, or the
    version it was made on), else the anchor's `lines` (a lost or pending
    range, or one whose block is gone, keeps the lines it was commented
    at); `quote` the block's (respelled by a rough placement) when the
    block is there, else the anchor's; `placed` reads the same ladder by
    `text_position.hash` in place of `doc_id`: `exact`, `rough` with
    `match`, `lost`, `pending` (measured on another version, the run not
    done) — never `estimate` or `earlier` (a text run writes no `approx`
    without `match`, and ranges are always placed again); and when it is
    `rough`, **`was`**, the text the comment was made on (the quote the
    thread's anchor recorded, given when the range reads otherwise now; for
    a thread without one, `text_position.prev.quote`, the text last
    confirmed there), so an agent that rewrote the sentence sees what was
    commented as well as what stands there now (a PDF entry has its
    `before` picture for this). On a secrets file
    (`anchors.text_kept`: `documents.text.is_secret`) neither quote is
    given, the lines alone, here and so in the files, the tools and the
    local agent's prompt.
- **`source`**: live (`version: "current"`) when the place is on a PDF
  page of the page's current file (`placed` is `exact`, `rough` or
  `estimate`) and the SyncTeX map answers on this computer ("SyncTeX");
  else the one recorded at the thread's open (`anchor.source`, `version:
  "commented"`); else null. `end` comes only when it differs from `line`. A
  region of a picture has none. A range of a text file is its own source,
  never SyncTeX's: `{"file": <the entry's file>, "line", "end"?, "version"}`
  from `where`, `current` while it is `exact` or `rough`, else `commented`
  (the lines it was commented at); `kept._record_source` never runs for it
  (it has no page).
- **`picture`** and **`before`**: "The pictures". HTTP and MCP add
  `picture_url` and `before_url` after them (`entries.with_urls`).
- **`link`**: `?ws=<ws>&page=<page_id>&block=<block_id>&thread=<id>`
  (without `&thread=` for a highlight), the address the Inbox opens, prefixed by `link_base` (MCP's absolute base;
  "" in the files and over HTTP, where the link is the query alone).
- **`comments`**: oldest first; `by` the author's username (the stored name
  once the account is gone), the person an agent wrote for; `agent` the
  label of the agent that wrote it ("Codex on TIM-PC", "Claude Code";
  [comments.md](comments.md) "Who wrote a comment") or "", `at` the
  comment's `created_at`, `body` ("" with `deleted: true` for a tombstone).
  An agent's comment adds its `agent_kind` (`gamma`, `computer`,
  `connection`); a person's has no such key.

### The order

Files by name (case folded, then as it is); in a file the whole file's
threads first, then by `where.page`, `anchors.top`, the box's left edge
(else the rectangles'), the entries without a page after those with one: a
picture's regions by top and left edge, a text file's ranges by
`where.line`, then the notes; ties by `created_at` (a thread's root's, a
highlight's block's), then id. Highlights and threads are one order.

### The document

```python
def entries(ws, conn, scope, *, state="open", paths=None, link_base="", sources=True,
            regions=None, places=False) -> list[dict]
def document(ws, conn, scope, *, state="open", paths=None, owner="", link_base="", sources=True,
             regions=None) -> dict
def entry(ws, conn, entry_id, *, paths=None, link_base="", sources=True, regions=None) -> dict | None
```

`sources=False` leaves the live SyncTeX lookup out (the recorded source
stays): a picture's read needs none. `regions`, a dict the caller passes, gains `{key:
(page id, Region)}` for every picture an entry names, so whoever draws them
(`pictures.cached`) knows what each key is. `entry` answers one thread in
any state, or a bare highlight by its block id; None when it is hidden
(orphaned, gone), a reply's id, a highlight that has a thread, or on no
live page.

```text
{"format": "gamma-annotations-1", "workspace": "<ws>", "work_folder": {"id", "name"} | null,
 "scope": {"kind", "id", "name"}, "owner": "<username>", "state": "open",
 "updated_at": "<the newest change among the entries: a thread's last_at, a highlight's updated_at>" | null,
 "count": n, "truncated": m (only when cut), "truncated_threads": k (only when threads were cut),
 "answer": {"reply": "POST /api/pages/{page_id}/comments {block_id, thread_id, body}",
            "resolve": "POST /api/comments/{id}/resolve {resolved: true}",
            "mcp": ["comment", "resolve", "highlight"]},
 "entries": [...]}
```

`work_folder` is null outside a work folder (a library page or folder,
the library); `answer` is the same in every document (`entries.ANSWER`),
the writing tools answering any page's threads. `owner` is the username of the account the document is written for (the
vault's maker in the files; the reader's own account over HTTP and
MCP): how an agent tells the person's comments from other members'. Past
`MAX_ENTRIES` (500) the document is cut: the threads fill the cap first,
then the bare highlights, and the entries kept stay in the document's
order. `truncated` says how many more entries there were and
`truncated_threads` how many of those were threads. No per-round time is written, so an unchanged document is
byte-identical, which is what lets the files' writer leave an unchanged
file alone.

## The Markdown

`markdown.render(doc, *, pictures)` writes a document as the Markdown an
agent reads, `pictures` one of `files` (`![…](img/<key>.jpg)`, the files
beside `open.md`), `urls` (the entries' `picture_url` / `before_url`, the
routes) or `tool` (no image lines; a line `Picture:
read_annotation(id="<id>")` instead). `render_entry(entry, *,
pictures, number=None)` writes one item, what the entry route's Markdown
and `read_annotation` give. Exactly:

```markdown
# Annotations on paper

5 open comments · 1 highlight · owner tim · updated 2026-11-10 13:58

Each item is a comment thread or a highlight. Answer a thread in Gamma: reply with what you changed
and where, resolve it when it is done (see "How to answer" at the end). A highlight marks a place,
with the note written on it: read it as context.

## main.pdf

### 1. Page 3 · “the loss is defined as”

- id: `0d7c…` · passage · placed exactly
- source: `sections/method.tex`, lines 118–120

![Page 3, the comment's region](img/3f9a2c1b7d4e6a01.jpg)

> **tim** · 2026-11-10 13:58
> lambda is never introduced.

> **Claude Code** (tim's connection) · 2026-11-10 14:06
> Defined in 3.1 (sections/method.tex:112).

### 2. Page 5 · area

- id: `a81e…` · area · placed by estimate · the figure was rebuilt
- source: `main.tex`, line 18 (as commented)

![Page 5, the comment's region now](img/…jpg)
![Page 5, the region as it was commented on](img/…jpg)

> **tim** · 2026-11-10 13:59
> move the legend inside the plot.

### 3. Page 7 · “we leave this to future work”

- id: `H9` · highlight · passage · placed exactly
- note: “cite the follow-up”

![Page 7, the highlight's region](img/…jpg)

## figures/fig1.pdf

### 4. The whole file

- id: `…` · file

> **tim** · …
> use the viridis palette.

## main.tex

### 5. Line 17 · “This note measures how much it helps”

- id: `5e0b…` · lines · placed exactly
- source: `main.tex`, line 17

> **tim** · 2026-11-10 13:56
> say by how much.

### 6. Lines 26–27 · “Training and test loss per epoch, without any weight decay.”

- id: `6a1c…` · lines · placed roughly (0.91)
- was: “Training and test loss per epoch, with no weight decay.”
- source: `main.tex`, lines 26–27

> **tim** · 2026-11-10 13:55
> name the dataset.

---

## How to answer

- Reply: `POST /api/pages/{page_id}/comments` with `{"block_id", "thread_id", "body"}`, or the `comment` tool with `thread_id`.
- Resolve: `POST /api/comments/{id}/resolve` with `{"resolved": true}`, or the `resolve` tool.
- Ask or point something out: the `comment` tool on a place (a PDF quote with its page, a file's lines, a note), and `highlight` to mark a passage.
- The JSON form with every id is `open.json` beside this file.
```

The heading is the scope's name ("the library" for the library); the line
under it counts the threads and the highlights and names the owner, the newest activity and, past the cut, `{n} more not
listed`. Item headings: `Page {n} · “{quote, at most 80 characters}”` (a
passage), `Page {n} · area`, `Page {n} · handwriting`, `Page {n} · note on
“{quote}”` (a note under a passage, `note on an area` and `note on
handwriting` under those), `Region · {w}×{h} px at ({x1}, {y1})` (a region
of a picture, by `where.rect`; `Note on a region · …` under one; `Region`
alone when the block is gone), `Lines {line}–{end} · “{quote}”` / `Line
{line} · “{quote}”` (a range of a text file; `note on “…”` under one,
`passage` for an empty quote), `A note` (one without a place), `The whole
file`. A bare highlight's heading is its place's, never `note on`, and its
facts line adds `highlight` after the id
(`- id: `H9` · highlight · passage · placed exactly`), its note on the line
after.
`placed` reads `placed exactly` / `placed roughly ({match})` / `placed by
estimate` / `not found in the current version` / `not placed yet` / `drawn
on an earlier version` — one vocabulary for every format, a build's PDF and
a source file alike, as the app's thread card says it — and for a region
`placed exactly` / `drawn on an earlier
version of the picture` / `the file is no longer a picture`; a rough range
adds the line `- was: “{the text it was made on, at most 80 characters}”`
after the note; an item with a
`before` picture adds `the figure was rebuilt` (an area), `the PDF was
rebuilt` (a passage) or `the picture was rebuilt` (a thread on a picture's
page); a resolved item says so. The source line reads `lines a–b` when
`end` is there and `(as commented)` when its `version` is `commented`. The picture lines read `Page {n}, the comment's region`
(`the highlight's region` for a highlight)
(`now` when a `before` follows) and `Page {n}, the region as it was
commented on`, without the page for a region of a picture, and `The
picture` / `The picture as it was commented on` for a thread on a picture's
page. Bodies are quoted line by line with `> `, a body's own Markdown left
as it is; a tombstone reads `*(deleted)*`. An agent's comment is signed
with its label and the person it wrote for, `**Claude Code**, for tim`,
`**Codex on TIM-PC**, for tim`. The last line of "How to answer"
names the JSON form the reader can reach (`open.json`, the `GET
/api/annotations` query, or `read_annotation`). With no entries: the
heading, `No open comments and no highlights.` (or `No resolved comments
and no highlights.`), and "How to answer". The
renderer has no i18n: the files are for agents, in English, the one text
Gamma writes untranslated ([i18n.md](i18n.md)); everything a person reads
in the app is translated.

## The pictures

`gamma/annotations/pictures.py`: the passage, area or handwriting a thread
or a bare highlight is on, cut out of its PDF page as the page shows it;
the region of a picture, cut out of the picture; the whole picture for a
thread on a picture's page. A passage, an area, ink, a region and a note
under one of those get pictures, and the page's thread when the page shows
a picture; a note alone, a PDF's whole file and a text file (a range of
lines, a note on one, the whole file: its entry names its lines, and its
`was` stands for `before`) none.

- **`picture`**, the region as the person sees it now: `pictures.now(conn,
  page_id, props, anchor, memo=)` when `placed` is `exact`, `rough` or
  `estimate`; null for `lost`, `pending` and `earlier`. It is the page's
  current file with its pictures, text boxes and every non-embedded ink
  group drawn as `ink_view.page_with_handwriting` draws them, and the
  page's highlights that are not `still_embedded` and were measured on
  this file (a `pdf_position.doc_id` absent or equal), the approximate ones
  included, the lost ones not, drawn as annotations through
  `pdf_export.page_with_ink(highlights=…)` (`_mark_annotation`, a
  `/Highlight` or a `/Square` for an area; [ai.md](ai.md) "Highlights
  drawn"). On a picture it is the current version cut at the region's box
  (the whole picture, `[0, 0, 1, 1]`, for the page's thread) with the page's
  regions drawn as the viewer draws them (an outline in the colour's line
  tone over a 25 % wash), whatever version the region was drawn on
  (`placed: earlier` too: a region stays where it was on the new picture).
- **`before`**, the region as it was commented on: `pictures.made(anchor,
  color)` when the root keeps an anchor whose `doc_id` is not the page's
  current file and that version is still stored (`storage.exists(ws,
  anchors.held_file(anchor))`, "The commented version kept"); else null. It
  is the held version's page with its pictures, text boxes and ink as they
  are (page coordinates do not move) and the anchor's rectangles alone, in
  the thread's block's colour; a handwriting group is drawn as the page's
  ink, with no mark over it (a wash over a drawing's bounding box would not
  be the region as commented on), its key still naming the anchor. On a
  picture it is the held version cut at the box with the thread's own
  rectangle drawn, stronger; the whole held picture for the page's thread.
  A root from before step 54 has no `before`.
- **A region** is `Region(file, page, box, drawn)`: `file` the stored file
  it is cut from (`<hash><ext>`: a PDF, or the picture itself), `page` its
  1-based PDF page (0 for a picture), `box` the crop, `drawn` the marks'
  signature: each highlight by id, colour, kind, style and rectangles, each
  ink group by its content-addressed file, each picture and text box by its
  place and content, the embedded originals they replace; on a picture each
  region by id, colour, rectangle and whether it was drawn on this version;
  for `made`, the anchor alone. It is computed once per file and page in a
  pass (`memo`) and shared by the page's threads. **The key**,
  `pictures.key(region)`, is 16 hex digits of the SHA-256 of `[PICTURE_V,
  file, page, box, RENDER_MAX_SIDE, drawn]`, a PDF going in by its stem so
  step 2's keys stand: the same inputs give the same key, a mark moved on
  that page another, a mark on another page none. Names are
  `img/<key>.jpg`.
- **How**: one one-page PDF per `(file, page, drawn)`, then
  `pdf_text.render_page(data, 1, RENDER_MAX_SIDE, box)`: the longer side
  1568 px, a zoom of at most 4, JPEG quality 85, never re-encoded. A
  picture goes through `ai_pictures.image_region(ws, file, box, marks)`
  ([ai.md](ai.md) "Pictures"): the same side and zoom, a crop of at least
  48 of the picture's own pixels a side. A page past the end, an unreadable
  or encrypted file, a picture that does not decode or a half-written build
  gives None: no picture, and the next pass tries again.
- **The cache**: `cached(ws, conn, page_id, region)` reads data.db
  `annotation_pictures` by key, else renders and keeps, the oldest going first
  past `CACHE_BYTES` (48 MB; [user_db.md](user_db.md) "data.db"). In a work
  folder the file `img/<key>.jpg` is the cache too. Pictures are derived,
  never uploads.

## SyncTeX

Where in the sources a place on a work folder's PDF was typeset: an entry's
`source`. `gamma/annotations/synctex.py` is the reader (pure, stdlib only), and
`gamma/annotations/sources.py` maps a place to `{"file", "line", "end"?}` on
this computer. The build must write a SyncTeX file (`-synctex=1`; latexmk
and most editors do).

- **The file** (`sources.find(pdf, root)`): `<stem>.synctex.gz` or
  `<stem>.synctex` beside the PDF (the newer when both), then in the build
  directories beside it (`build`, `out`, `output`, `_build`, `.build`,
  `latex.out`, `tex.out`) and in the `$out_dir` / `$aux_dir` a `latexmkrc`
  or `.latexmkrc` beside the PDF or at W's root names (never outside W),
  then the one `*.synctex.gz` of those directories that passes the gates (a
  PDF copied under another name). Never a walk.
- **The gates**, at every use: the PDF on disk is the version Gamma shows
  (`storage.linked_path`: its size and time are the recorded ones); the
  SyncTeX file has as many sheets as the PDF has pages; the two were
  written within 120 s of each other; and no `<stem>.synctex.gz(busy)` is
  newer (a build running: no source now, the next pass tries). The file is
  read whole and closed at once, so the next build's rename over it never
  fails on Windows; at most 64 MB compressed and 256 MB of text; the last
  four files are kept parsed while their size and time hold, a sheet parsed
  on first use.
- **The map**: the rects are put in SyncTeX's frame (`sources.tex_rects`:
  big points from the media box's top-left, y down, through the page's view
  box and rotation, from one pypdf read of the PDF), and `SyncTeX.query(page,
  rects, text=)` walks the boxes meeting them, weighting each hbox by its
  overlap (a passage is asked as text: each rect's vertical middle third).
  Every word of a paragraph sits in an hbox tagged with the line where the
  paragraph ended, so a line is read from the leaves: a stretch of text
  belongs to the line of the interword space after it (a glue, or a kern of
  a point or more), else to the end of its run; a leaf tagged with the
  sheet's first record, the line TeX was reading when the page shipped out
  (the page number, a footnote rule), says nothing while another leaf can,
  and ranks last. A place on no box within 12 bp of a line of text is that
  line's. Rotated or scaled content (pdflscape, `\rotatebox`) is recorded
  untransformed, meets no box and answers nothing. So a passage maps to its
  lines; a figure's area to the line that includes it (`\includegraphics`,
  through the `figure` environment's lines); a display equation to its
  `\end{equation}` line; a page number or footer to the shipout line, which
  only wins when nothing else meets.
- **From tags to a path** (`sources.project_path`): each tag's path is
  taken below W's directory, else by the longest tail of its parts that is
  a file under the PDF's directory or a directory above it up to W (a
  build made in a container or another checkout, whose PDF may lie in a
  subdirectory or an `out/` directory), else dropped (TeX's own files).
  The source file with the largest share wins; a
  generated one (`.aux`, `.toc`, `.bbl`, …) only when there is none. Its
  lines with at least 5 % of its share give `line` and `end`.
- **Where it answers.** The page must be a file whose work folder has a
  local link (`folder_links.vault_of_page`) and whose PDF is on this
  computer; a place carrying another file's `doc_id` is never mapped
  against the current build. So the owner's desktop answers live sources;
  the NAS, a clone or a vault of another server show the recorded ones
  only. The answers are memoized by the files' stats and the place (2000 of
  them), so a pass that reads the same entries again costs a few stats
  until the next build. Tests: `tests/test_synctex.py` (a truth corpus per
  engine, `tests/samples/synctex/`) and `tests/test_annotations_sources.py`.

## The commented version kept

A page whose document is read in place (`documents.model.in_place`: a work
folder's build or figure, an Opened file, a vault's PDF) has no stored copy
of its bytes, and the next build at its path takes them away. While such a
page has an open thread it keeps the bytes it shows stored
(`uploads/<hash><ext>`: a PDF's `.pdf`, a figure's `.png`), so the version
the thread was opened on stays readable: the relocation run places a PDF's
highlights again from it
([highlight_relocation.md](highlight_relocation.md)), and the reader draws
the `before` picture from it. `gamma/annotations/kept.py`:

- **At a thread's open** (`after_comment`, a `create` event) one daemon
  worker, never the request, stores the page's current bytes from its path
  (`linked_files.store_page_bytes`, hash-checked: a build changed under the
  read is logged and that version is gone) and claims the name
  (`upload_gc.claim`); then, when the page is a file and the anchor's
  `doc_id` is its current file, records the source line of the place on the
  root's anchor (`anchor.source`), which the next build's SyncTeX file no
  longer describes. A thread opened on another server's page records none,
  nor does one on a picture.
- **At every replacement while it holds** (`keeps_bytes(ws, page_id,
  props)`: the page is a library file, or a PDF or a picture with an open
  thread, below) the incoming
  bytes are stored first (`vault.local_remote.LocalRemote.replace_doc`,
  `linked_files._take_bytes`), so the outgoing ones, stored at the open or at
  the replacement before, are the run's reference and get the replacement's
  short life (`upload_gc.retain`). Every build shown while a thread is open
  is stored: a second rebuild would otherwise lose an area's own file.
- **Repair** (`repair(ws, page_ids)`) stores what a busy file, the quota or
  a crash between the commit and the worker left unstored; the files'
  writer calls it on every pass and `linked_files.sweep` for Opened files. A
  quota refusal is logged once per change of state.
- **The hold**: an open root's `anchor.doc_id` and `anchor.ext` name a held
  reference (`anchors.held_file`, read by `upload_gc._references` into
  `names`, never `shown`; [user_db.md](user_db.md) "What counts as a
  reference"). A resolve releases it (`upload_gc.schedule`), a reopen
  claims it again (a version purged meanwhile stays gone; the card's Before
  says so); a delete, an orphaning or the sweep is left to
  `upload_gc.reconcile`. A region's `image_position.hash` is no reference
  of its own: the version a thread on it was commented on is held by the
  anchor alone.
- **What leaves the computer**: a stored copy of a work-folder file that is
  not a library document is named by no mirror, share export or publication
  (`storage.upload_refs(held=False)`); backups and the off-site copy carry it
  as any stored file, and the quota counts it. The backup merge and the
  owner's Gamma export add the copied roots' held names.

A highlight made on a build replaced before its thread was opened is placed
by its quote, as any highlight on a file read in place: nothing is stored at
the first highlight.

Only a PDF's and a picture's versions are kept: `kept.KEPT_FORMATS = ("pdf",
"image")`, the formats whose threads need the version they were opened on
(the PDF run reads the text under rectangles from it, the pictures draw
`before`). A range of a text file carries its own context, its quote and
the text around it ([api.md](api.md) "The highlight shape"), so its run
reads nothing of the old version and it has no picture: a thread on a
`.tex` stores nothing at its open (`_unstored` asks the format, so `repair`
follows) nor at a replacement (`keeps_bytes`' open-thread case asks it,
whether the caller passes the properties or not), and a `lines` anchor
holds no file. A work folder's file in the library keeps its bytes whatever
its format, as a library document does.

## The files

For a work folder of this computer, and for a page that follows one file
of it (a vault of one file, [synced_vaults.md](synced_vaults.md) "A vault
of one file"), the annotations are files on the disk too, so an agent at
the terminal reads them with no connection to Gamma.
`gamma/annotations/files.py` writes them in a hidden directory of Gamma's
own ([work_folders.md](work_folders.md)); `files.place(link)` says where:

```text
<work folder>/.gamma/annotations/
  .gitignore          "*\n": the repository's git never shows the churn
  open.json           document(W, state="open", owner=<the vault's maker>), 2-space JSON, UTF-8, "\n" at the end
  open.md             markdown.render(that, pictures="files")
  img/<key>.jpg       write-once by key

<the file's directory>/.gamma/annotations/<file name>/
  .gitignore, open.json, open.md, img/    the same, of the followed page (its scope a page's, its file the file's name)
```

The open threads and the bare highlights are listed, the threads counted in
the summary. A followed file's directory is named by the file's own name,
so two files followed in one directory never meet; the `.gamma/` beside it
holds no marker, so nothing takes it for a vault (`vault.disk.read_marker`
answers none, *Open with Gamma* names nothing of Gamma's there), and no
vault is made of a `.gamma` directory or inside one
(`folder_links.place_refusal`, `OWN_REFUSED`). `vault.disk.write_own` takes
the followed file's name (`beside`) where a vault has its marker: the file
there is what makes the directory one to write in, but where that
directory's `.gamma/` holds a marker (a vault this server does not know:
one removed while its disk was away, another Gamma's on this computer)
nothing is written and the summary says why (`Stopped`); the `.gamma/` it
makes is hidden as a vault's is. A directory no vault may have
(`place_refusal`: a drive's root, the home directory or one around it,
Gamma's data directory, a system or temporary place) gets no files:
`folder_links.annotated` is false there. A work folder's files were below
`.gamma/feedback/` before step 3.2, and its Sends' request files in
`.gamma/annotations/requests/` until step 3.7 (`files.FORMER`): every write
and every removal deletes those directories where they are still there.

- **When.** (a) Every whole round of a work folder
  (`folder_links._vault_round`, after `vault.sync.sync`, under the link's
  lock; one of another server too, `work_vault`), when the round's status
  has no `stopped` or `offline` and the vault is not choosing its files,
  and every round of a vault of one file (`_file_round`, after its push);
  the summary goes into `status["annotations"]`; so a new vault's first
  round makes the directory, with an empty document, before any thread.
  `folder_links.annotated(link)` is the one test of both: a work folder, or
  a vault of one file that no folder vault's directory holds (one
  `_take_in` could not take in, whose `.gamma/` may be that vault's: nothing
  is written twice) in a directory `place_refusal` allows. `_take_in_one`
  removes a file vault's files under that vault's lock, so a pass that was
  writing them ends first. (b) The hot loop (`folder_links.hot_tick`) writes the
  scopes `files.due(HOT_S)` names, those marked at least that long ago: a
  work folder W, or the page a vault of one file of this server follows
  (`_annotations_pass`). A link resting because it is paused, failed or
  choosing drops the mark (its next whole round writes), a held lock keeps
  it, otherwise `write(link)` under the lock, then `settle(ws, scope, at)`
  with the monotonic time the pass began, so a mark newer than the pass
  stays. `due` keeps the newest mark of each scope (a burst of writes is
  one pass, once the last is `HOT_S` old). So a highlight, a comment, a
  reply, a resolve or a relocation reaches `open.json` within
  about 2–4 s. A vault of another server has no hot pass: its rounds are
  what read the files again, a work folder's at every whole round and, on a
  round of push alone, at most every `REMOTE_POLL_S`
  (`folder_links._annotations_read`; a round that skips them keeps the last
  summary), a followed file's every `REMOTE_POLL_S` while the file rests
  (`folder_links.due`). The document is asked for within `ASK_TIMEOUT_S`, a
  question between rounds, so a silent server holds the link's lock no
  longer than that; its pictures wait as a round's do.
- **What `write` does**, in order: the place answers as the vault's
  (`vault.disk.check_marker` for a folder vault: a copy of the directory or
  an empty mount never gets files; for a vault of one file the file there,
  `gamma_sync.NOT_THERE` otherwise, and on this server its maker still a
  member of the workspace); the document: on this server
  `entries.document`, sources on, of W named by the vault's rows, or of
  the followed page named by the file's name, then `kept.repair` for the entries' pages; for a
  vault of another server `GET /api/annotations?scope=<its folder or
  page>&state=open` there with the vault's token (`files._far`: cut to the
  document's own fields, each picture named by its key alone and its route kept to fetch it, a file
  named by the vault's rows where they name its page, the entries sorted by
  file again; an answer of another format is an error). Then the pictures
  the entries name that `img/` lacks, drawn through `pictures.cached` or
  fetched from that server's `GET /api/annotations/{id}/picture` (a JPEG
  of at most `MAX_PICTURE`), and written with `vault.disk.write_own`, at
  most `PICTURES_PER_WRITE` (20) a write (a picture not written yet is null
  in this write's document, and the next pass fills it; one that cannot be
  had now, a build being written, waits for the next whole round);
  `.gitignore`, `open.json` and `open.md`, each written
  only when its bytes differ from the file's (a replace that keeps failing
  under a reader on Windows is left for the next pass, and is no error of
  the vault's); `img/` pruned of the pictures named by neither this
  `open.json` nor the one it replaced (a work folder of
  another server's is not pruned while an agent works in it,
  `runner.at_work`: its turn fetched pictures there that no document here
  names, `local_agents/home.py` `_pictures`); Gamma's own stale
  temporary files in the files' directory and `img/` swept
  (`vault.disk.sweep_own`; the vault's scan never enters a dot directory);
  a work folder's `FORMER` directories deleted. The summary is `{"open": n}`,
  the open threads, with `"left": m` when `m` pictures or files wait for
  the next pass (the cap, a reader's hold), which the hot pass reads to
  keep the mark; errors are caught into `{"open": n, "error": …}`, logged
  once per change of text, and settle the mark (the next whole round
  reports them again), so a copy of the directory is not retried every
  2 s, and a round never fails for its annotation files: another server
  that does not answer, or one of a release before the reader, is that
  summary's error alone.
- **The token** of a vault of another server is an integration token of
  that server ([synced_vaults.md](synced_vaults.md) "A vault on another
  server"): a member's read of the workspace there, which `GET
  /api/annotations` and its pictures are for every token of any scope in
  its own workspace ("The routes"). Nothing more is asked of it.
- **Removal.** `files.remove(link)` deletes a vault's files: from
  `folder_links._remove_vault`, when the marker is ours, before the marker
  is unlinked; from the release of a work folder whose push was lowered
  (`_work_turn`) and the round of one of another server that finds it let
  go there; for a vault of one file from its removal (`delete_link`), from
  a relink, beside the file that was, and from a folder vault that takes it
  in (`_take_in_one`), then `.gamma/annotations/` and `.gamma/` beside the
  file when that leaves them empty. The files are Gamma's and regenerable:
  deleted directly, never through the trash; a link or a junction planted
  inside the directory is unlinked, never entered. A paused vault writes
  nothing between rounds (the pause means "leave the directory alone"); the
  HTTP routes are unaffected.

## What keeps it current

`gamma/annotations/listeners.py`, registered by its `register()` from
`app.create_app`, so every server process has them:

- On every committed comment write (`comments.write_listeners`,
  [comments.md](comments.md) "The API"): `files.mark(ws, page_id)` (the
  files of the page's work folder, or of the vault of one file that follows
  it, are due; cheap, no IO; nothing on a `sweep`
  event, which names no page) and
  `kept.after_comment(ws, event)`, each in its own `try` with its module
  loaded when first asked for, so a missing piece or a failing one never
  stops the others, and none fails the write.
- On every committed op batch (`ops.commit_listeners`): `files.mark(ws,
  page_id)`, whoever wrote it, a vault included: a relocation, a trash or a
  restore and an orphaning delete move an entry, and a highlight made or
  changed is one. Nothing is read here; `files.due` resolves the marked
  pages to their work folders (the pages' `work_folder` column) and to the
  pages followed by a vault of one file of this server (users.db
  `folder_links`), in one query of each per workspace.

Both parts mark only on a server that keeps folders on disk
(`config.folder_links_enabled`); the routes read live rows and need no
mark.

## The routes

`gamma/routers/annotations.py`, prefix `/api/annotations`. Every route is for the workspace's members of any role and for
integration tokens of any scope, a token in its own workspace as on every
route of the HTTP API; a share visitor, signed in or not, gets 403
"annotations belong to the workspace's members". Every handler is a sync
`def`: it reads the pages.db, and the pictures are drawn, off the event
loop. `AnnotationError` becomes the `HTTPException` of its status.

| Method | Path | Answer |
|---|---|---|
| GET | `/api/annotations?scope=<id>&state=open\|resolved\|all&format=json\|md` | the document of a page, a folder or the library (`scope` a page's or a folder's id, or `home`; `entries.document`, `owner` the reader's username, the entries with `picture_url` / `before_url`), or with `format=md` its Markdown (`text/markdown; charset=utf-8`, the pictures as their routes). 400 for another state or format; 404 for no such folder or live page |
| GET | `/api/annotations/{id}?format=json\|md` | `{entry}`: a thread's in any state, or a bare highlight's by its block id; or its Markdown (the file's heading, then the item); 404 for one hidden, gone, a reply's id, a highlight that has a thread, or on no live page |
| GET | `/api/annotations/{id}/picture?at=now\|before&v=<key>` | `image/jpeg`, `Cache-Control: private, max-age=86400`; 404 when the entry has no such picture or it cannot be drawn now; 400 for another `at`. `v`, the picture's key, only busts a browser's cache |

`picture_url` is `/api/annotations/{id}/picture?at=now&v=<key>`,
`before_url` the same with `at=before`.

## The writing tools

What an agent writes on a page, as itself for its account: `comment` (a
reply in a thread, or a new thread on a place), `resolve` and `highlight`,
three tools of the chat registry ([ai_tools.md](ai_tools.md) "comment /
resolve / highlight") that Gamma AI has under its Comments and highlights
permission, an MCP connection at the Comment level ([mcp.md](mcp.md) "The
Comment level") and a computer agent's turn through its token. They work on
every page of the library, a work folder's file as a library page.

**The places** (`places.find`): a PDF passage by its quote and its 1-based
page, an area of a PDF page by its `box` (page fractions) and page, a range
of a text file by its first and last line (with the quote of those lines
when it comes, else the lines whole), a note by its block, the page itself
by nothing. An area is the rectangle a person's Ctrl+drag makes, an area
highlight with no quote in the page's own frame (`pdf_text.page_sizes`).
The passage finder is `places.passage_at`, which the chat's drawing tools
anchor to as well ([ai_tools.md](ai_tools.md) "Drawing on pages"). A PDF quote is looked for on its page and the ones
either side (`highlight_relocate.place_quote`: `find_passage` over those
pages, the named one first among equals, the text folded as a relocation
folds it) and drawn over the glyphs it covers (`rects_for_span`, the page's
display frame, `highlights.position`); a range's quote near its lines
(`text_anchors.find_range`, its fold), its context written as a selection
writes it (`highlights.span_text`). The agent copied the quote from the
text, so an exact copy (as the fold reads it) places it and nothing less: a
rough match, however alike, is the closest passage, and the call is refused
saying it ("that quote is not on PDF page 3 or beside it; the closest
passage reads “…” (92% alike)", a range's with its lines), or with no closest
at all, never placed by guess. A secrets file's text is never said back
(`anchors.text_kept`), and a quote on one is refused before any match, the
same words whether the text is there or not, so no answer tells what the
file holds: its lines are named with `line` and `end_line` and marked
whole. An exact copy with no glyph drawn over it (a scan's hidden text
layer) is refused too. The quote is stored as the file spells it.

**The writes** (`hand.py`, a `Writer`: the account, its username, the
agent). A passage or a range becomes a highlight block like a person's: the
page's last top-level block, `{color, quote, pdf_position | text_position,
style?, agent}` (`agents.stamp`), written through `ops.apply_ops` as the
account with client `ai` — so the reader lists it back as any highlight,
the files on a vault's disk carry it, and the PDF and the code view draw it.
A comment's passage is drawn in the wash, as a person's commented passage
is; a highlight takes a colour of the default washes (`hand.COLORS`) and a
style of the viewer's (`hand.STYLES`). A thread is written through
`comments.create` on that block, the note or the page root (its anchor
computed as the API's), a reply on the thread's block, a resolve through
`comments.resolve` (never a reopen); `comments.written` and
`ops.after_commit` follow as for a person's write. Before each write the
hand checks the server takes writes (`hosted.read_only`), no delete holds
the workspace, and the account may comment there (any role); a new
highlight, a `highlight` or a comment on a passage or a range, is a block
of the page, an edit, so the account must also edit there (`hand.may_mark`,
checked in the approval's preview too): a viewer's agent comments on the
page or a note, as a viewer does. A thread the op path or the comments
refuse after its passage was marked takes the new highlight away again. A
thread is any live root on a page of the library (`hand.thread`).

**Made again.** The same agent's same words for the same account on the
same place within
`hand.REPEAT_S` (five minutes) are one call made again: the same thread's
reply, a new thread on the same block or on the same passage (its page and
quote) or range (its lines). It writes nothing and answers with what the
first wrote (the tools say so as a refusal, with its ids and link), so a
retried call never doubles a comment. A place keeps one highlight of each
agent per account (the stamp's `id` and `for`): a second is refused with
the first's id. Gamma AI's id is the same for every account, so another
account's Gamma AI highlights that place too.

**The answers** name the file and the place, the ids made (the thread, the
comment, a new highlight) and the entry's link as `list_annotations` gives
it (`entries.link_of`).

## Tokens and MCP

- **The Comment level** (`integration_tokens.comments`,
  `integrations.comment_grant`): a `read` token comments, replies and
  resolves in the workspaces it names, a viewer's too, and highlights or
  comments on a passage or a range only where the account edits
  (`hand.may_mark`); on the HTTP API it comments in its own
  `workspace_id` (`auth.token_comments`); a `write` token
  comments in its workspace, a turn's token in its own; a viewer may make a
  `read` token at any level but Edit (viewers comment), `write` stays the
  editors'. `auth.require_commenter(request, page_id)` lets such a token
  write any comment the account may, and refuses a token that may not
  ([comments.md](comments.md) "Who may comment"). Before migration step 61
  it was the `feedback` scope, a reply and a resolve on a work folder's
  files alone; the step made those tokens `read` ones at the Comment level in
  every workspace they read.
- **The stamp.** A comment written through any token stores the token's
  display name and id (`agent`, `agent_id`; `resolved_agent` on a resolve),
  shown as the comment's author with the person's account; a token edits
  and deletes only what it wrote, and a session of the account deletes its
  tokens' comments. A token's comment makes no one follow the page
  (`ops.UNFOLLOWED`'s rule), and the Inbox tells the person of it as of
  anyone else's write, each item naming the agent ([comments.md](comments.md)
  "The inbox"). A retry of a comment id is the same writer's: a session's
  id retried by a token is a 409.
- **MCP**: `list_annotations` and `read_annotation` are reading tools of
  the chat registry (`ai_tools.TOOLS`; [ai_tools.md](ai_tools.md)), on
  every scope the chat and MCP have, reached on any connection and in the
  chat; the writing tools too, listed for a connection at the Comment level
  only and checked per call ([mcp.md](mcp.md) "The Comment level").
  `gamma_link(..., thread_id=)` writes the address the Inbox opens. A
  browser sign-in of a client that asks for `gamma:feedback`, the level's
  name, starts at Comment on the consent page ([mcp.md](mcp.md) "Browser
  sign-in protocol").
- **The plugin skill** (`plugins/gamma/skills/gamma/SKILL.md` "Highlights
  and comments") tells the agent the loop: find the items (`list_annotations`,
  or `.gamma/annotations/open.md` in a repository), read each with its source
  line, change what it asks for (the sources, then the project's build:
  Gamma builds nothing, it shows the new PDF and finds the comments again),
  reply with what changed and where and resolve what is done, leave what
  needs the person's decision open with a question, ask about a passage with
  a comment on it, highlight only on request, act on the owner's comments
  and ask about anyone else's, never answer its own replies, and never edit
  anything under `.gamma/`.

## The seam

On the desktop app's own server Gamma itself can be the connector: the
vault's maker turns on Codex or Claude Code for a work folder in the sync
panel (or for the library), and from then on a comment the maker sends to
the agent (the composer's Send to tick, or `@codex`) is a turn of its
thread's conversation, worked by the agent in the vault's directory and
ended by its reply in the thread, signed by its label
([local_agents.md](local_agents.md) "A comment sent to an agent"). A work
folder of another server (a NAS) whose keeping computer announces an agent
for it works the same way from that server: the turns sent there are taken
by that computer over its channel and answered there
([local_agents.md](local_agents.md) "Another server's chat",
[computers.md](computers.md)).

Anywhere else, Gamma starts no process. The owner builds the part that
starts an agent (a watcher, a timer, a shell hook), and what it finds here
is:

1. **What to do.** A work folder's `.gamma/annotations/open.json` and
   `open.md` on this computer ("The files"), rewritten within seconds of a
   comment, so a watcher sees them change; on any server, `GET
   /api/annotations?scope=<id>` with a token of the workspace (the JSON),
   `&format=md` (the Markdown), and `GET /api/annotations/{id}` for one
   entry. Each entry carries its id, file, place, source line, pictures and
   comments ("An entry"); the document's `owner` says whose comments are
   the requests, and its `answer` names the routes below.
2. **Answering.** Through the comment API with an integration token that
   comments in its own workspace (a `write` one, or a `read` one at the
   Comment level), each entry carrying its `page_id` and `block_id`: a
   reply is `POST /api/pages/{page_id}/comments {block_id, thread_id,
   body}`, a resolve `POST /api/comments/{id}/resolve {resolved: true}`; or
   over MCP with `comment` (its `thread_id`) and `resolve` at the Comment
   level. Each answer is signed with the token's name and reaches the
   person's Inbox; a resolved thread leaves `open.json`.

## In the app

The client's side is the comments' ([comments.md](comments.md) "In the
app", [ui-design.md](ui-design.md)): an ink group takes a thread (Comment
in the lasso's menu, a card in the PDF margin level with the stroke; a group
with a thread takes no new strokes, is never deleted by the saver and
refuses a merge, [handwriting.md](handwriting.md)); a thread's card shows
its agent's comments under the token's name, the chip of the reader's
turn of an agent while one is to end on it ([comments.md](comments.md)
"Sent to an agent"), and whether its passage moved or was not found in the
new version; the composer's Send to tick sends a comment to an agent
([local_agents.md](local_agents.md) "A comment sent to an agent"). The
Inbox names the agent on its items. The user guide's "Comments and inbox"
says it for people.

## Known gaps

- Ink threads are not placed again after a rebuild: an ink entry reads
  `placed: "earlier"` and leads with its `before` picture. A region of a
  picture is never placed again either ([highlight_relocation.md](highlight_relocation.md)
  "Regions of a picture"): it stays where it was, its entry reads `placed:
  "earlier"` with the current version's crop as `picture` and the held
  version's as `before`.
- The source is recorded at the thread's open and read live on this
  computer only; the NAS, a clone or a vault of another server show the
  recorded ones.
- An area is found again by the picture inside it only for raster images;
  LaTeX's vector figures are found by their text or ride along, and the
  `before` picture carries the meaning.
- Not built: a figure's own file mapped to the line that includes it;
  widening an `\end{…}` line to its `\begin{…}`; the `\includegraphics`
  path in `source`; re-finding a vector figure by its SyncTeX line after a
  rebuild.
- Comments made in a phone's clone, on the share host or on a publication
  never reach the desktop's files or its agent: comments do not travel.
  The routes of the server that holds them serve them. A vault of another
  server (the NAS) is the exception: its rounds read that server's
  annotations there and write them on this computer ("The files"), and
  its agent answers them through the computer's channel.
- The comment API's thread carries `made_on` (the anchor's `doc_id`), not
  the anchor; the reader reads the stored anchors itself.
- Every build shown while a thread is open is stored: a thesis rebuilt
  fifty times in an afternoon stores fifty copies, released a day after
  they are replaced and their highlights placed.
- A picture past Pillow's decompression bound (`Image.MAX_IMAGE_PIXELS`,
  about 89 Mpx) or past `STORED_MAX_BYTES` draws no picture, and there is no
  setting for either. The chat's region picture of a picture page shows
  nothing until the image branch of `/api/ai/page-image` answers.
- A range of a text file has no picture and no `before`: a rough one's
  `was` is the text it was made on, a lost one keeps its quote and the
  lines it was commented at. A `file` or `note` anchor on a text page still
  names the page's version (`held_file` holds `<hash>.tex`), which is never
  stored for it, so the hold costs nothing but a stored library text
  document's old version kept while the thread is open.

## Tests

- `tests/test_annotations_anchors.py`: the anchor of each kind, a region's and
  the extension held, a range's lines and the words they are named by.
- `tests/test_annotations_entries.py`: every kind's `where` and `placed`, a
  picture's regions and the whole picture through a rebuild, a text file's
  ranges by their own lines through a change (exact, rough with `was`,
  lost, pending, the block gone), the file's
  name from the vault's rows and from the tree, the order, a
  tombstone, the cut, the scopes and their refusals, the states, a root from
  before step 54, the source live or as commented, the pictures' keys; a
  library folder, a library page and the library with their bare
  highlights (always listed, read by their block id); the
  Markdown of a fixed document byte for byte, the empty form and the
  picture modes.
- `tests/test_ai_image_region.py`: the picture a page shows as the server
  draws it (`ai_pictures.image_region`) and the page-image route of a
  picture page; `tests/test_annotations_images.py`, step 3's gate: a work
  folder's PNG, a region and a thread on it and on the page, their
  pictures in `.gamma/annotations/`, and a rebuild of the picture.
- `tests/test_annotations_routes.py`: JSON and Markdown, one thread, the
  picture route, who reads, a library folder, a library page and the
  library with a bare highlight.
- An agent on this computer has its own suites,
  `tests/test_local_agents_*.py` ([local_agents.md](local_agents.md#tests)).
- `tests/test_annotations_pictures.py`, `test_annotations_kept.py` (a text file's
  thread keeps no version), `test_synctex.py`, `test_annotations_sources.py`,
  `test_annotations_files.py` (a work folder's files written, rewritten and
  removed, the `FORMER` directories of before gone, a bare highlight listed,
  an entry on lines of a tracked `.tex`, a followed file's files beside it:
  written, rewritten, never a vault, gone with the vault, at a relink and
  at a take-in, none inside a folder vault's directory),
  `test_remote_work_folders.py` (a work folder and a followed file of
  another server, read there with the vault's token, a server before the
  reader the files' error alone),
  `test_annotations_tools.py`, and the gate `test_annotations_loop.py` (a work
  folder with a paper, two comments, a commenting token's answers, a
  rebuild, the Inbox, `open.json` empty at the end).
- `tests/test_annotations_hand.py`: the writing tools (each place, a quote
  not there refused with the closest, a reply, a resolve, a highlight in a
  colour and a style, a call made again, Gamma AI's permission and a
  viewer's chat, the Comment level of a read, a commenting, a write and a
  turn token).
- The browser groups `work-folders` (a highlight in `open.md` on the
  disk), `comments` (a thread on handwriting) and `local-agents` (a comment
  sent to an agent on a `.tex` and on a library PDF).
