# Feedback on a work folder's files

A comment thread on a file of a work folder is feedback: a request the
person makes about that file, which an agent in the repository (Claude Code
at the terminal, a script holding a token) reads and acts on. Nothing new is
stored for a thread to be feedback: it is open until someone resolves it, a
reply says what was done, and reopening sends it back
([comments.md](comments.md)). What this adds is what makes a thread usable
from outside Gamma: the place it was opened on, kept with it; a reader that
turns the threads of a work folder, a directory or a file into entries with
their file, place, source line, picture and comments, served as JSON and
Markdown over HTTP, over MCP and as files in the repository; the version of
the file (a build's PDF, a figure) a thread was opened on, kept while the
thread is open; Send, the person's "go"; and a token scope that answers
feedback and can do nothing else. Backend: the package `gamma/feedback/` and
`gamma/routers/feedback.py`. The plan is
[research/file-explorer.md](../research/file-explorer.md) section 7 (step 2
of "In steps"; step 3 adds a picture's regions and the whole picture); the
surveys behind the SyncTeX reader and the pictures are research notes.

| Module | What |
|---|---|
| `anchors.py` | the place a thread was opened on, kept on its root ("The anchor") |
| `entries.py` | the reader: scopes, entries and the document ("The entry and the document") |
| `markdown.py` | the Markdown form ("The Markdown") |
| `pictures.py` | the region pictures and their cache ("The pictures") |
| `synctex.py`, `sources.py` | the SyncTeX reader, and a place on a work folder's PDF mapped to its source ("SyncTeX") |
| `kept.py` | the commented version kept ("The commented version kept") |
| `requests.py` | Send: the requests and their states ("Requests and their states") |
| `files.py` | the files below a vault's `.gamma/feedback/` ("The files") |
| `listeners.py` | the hooks on comment writes and op commits ("What keeps it current") |
| `agent.py` | `reply_feedback` and `resolve_feedback` ([mcp.md](mcp.md) "Answering feedback") |

The words: **W** is a work folder's root folder
([work_folders.md](work_folders.md)); **a file** a page with
`properties.work_folder` (a tracked file, a page at its path); **a thread**
a root comment and its replies; **an agent** whatever writes through an
integration token; **the person** the account the token belongs to;
**open** a root neither resolved nor orphaned, a tombstone root that still
has replies included (`comments.open_counts`'s rule, [comments.md](comments.md)
"Threads"), so the list view's count, the entries, the hold on the
commented version and `comments.has_open_thread` agree.

Only a work folder's files have feedback. A library page's threads are not
(`resolve_scope` refuses a library page or folder, `entry` answers None for
such a thread); the shape needs no change if a later step opens it. Threads
by other members, and by signed-in accounts a folder share admits, are
feedback too: the entries say who wrote each comment (`by`) and the
document whose account it is written for (`owner`), and the agent's skill
acts on the owner's comments and asks about anyone else's
([mcp.md](mcp.md) "Answering feedback").

## The anchor

A root comment keeps where its thread was opened, `comments.anchor`, JSON
(`gamma/feedback/anchors.py`; the column is [comments.md](comments.md)
"Threads"):

```text
{"v": 1,
 "kind":   "passage" | "area" | "ink" | "region" | "note" | "file",
 "block":  "<the block the place is read from>",
 "doc_id": "<24 hex>" | "",          # the file the place was measured on
 "ext":    ".pdf" | ".png" | …,      # the page's document's extension when the thread opened; "" for none
 "page":   3 | null,                 # the 1-based PDF page; null for a region of a picture
 "rects":  [[x1, y1, x2, y2], …],    # fractions of the page (or the picture) as shown, top-left origin, 4 dp, at most 200
 "quote":  "…",                      # at most 1000 characters; "" for an area, ink, a region, a note, the file
 "box":    [x0, y0, x1, y1] | null,  # the region picture's crop, fractions, 4 dp
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
- **note**: anything else. The nearest ancestor below the page that is a
  highlight, a link region, an ink group or a region with a position gives
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
for NULL or a malformed one; a dict passes through), `held_file(text)`
(`stored_name(doc_id, ext)`, the stem through `storage.stored_pdf_stem`,
"" for none: the hold, "The commented version kept"; an anchor written
before step 3 has no `ext` and holds a PDF, one whose `ext` is no
extension holds nothing) and `top(anchor)` (the place's top: the highest
rectangle's, else the box's, else 0: the order of a page's threads).

## The entry and the document

`gamma/feedback/entries.py` reads; it writes nothing and renders nothing.

### Scopes

```python
class Scope(NamedTuple):
    kind: str            # "work_folder" | "folder" | "file"
    id: str              # W, a folder below W, or a file's page id
    work_folder: str     # W
    name: str            # W's name, the directory's path in W, or the file's path
```

`resolve_scope(conn, scope_id, tree=None)`: a folder id is W
(`work_folder`) or a folder below one (`folder`, named by its path in W);
a live root page with `work_folder` is a `file`. `FeedbackError(404)` for
no such folder or live page (a page in Recently deleted is none),
`FeedbackError(400, "Feedback is for a work folder's files.")` for a library
page or folder. `work_folder_named(conn, name_or_id, tree=None)`: a W by
its id, or by its name when exactly one carries it (404 for none, 400 for
two: name one by its id), by the work folder name rule of
`work_folders.paths.work_folders_named`: exactly, else ignoring case with
`lower()` ([work_folders.md](work_folders.md#paths)). `FeedbackError(status, detail)`
lives here; the routes answer it as an `HTTPException`, the MCP tools as an
error text.

### Which threads

`comments.threads_in_work_folder(conn, w, state=)` gives W's threads in one
query joined on the pages' `work_folder` column (`idx_ub_work_folder`,
[comments.md](comments.md) "Threads"): the live pages whose work folder it
is, so a file in Recently deleted and an orphaned thread are none. A
`folder` scope keeps the files whose `folders[0]` is in
`marks.Tree.subtree(scope.id)`; a `file` scope reads that page
(`comments.threads_for`). `state` is `open` (the default), `resolved` or
`all` (`FeedbackError(400)` otherwise). `thread_ids(conn, scope, *, state)`
gives the ids, oldest first: what a Send names when it names none.

### A file's name

`file_path(tree, props, paths=None, *, page_id, title)`: `paths[page_id]`
when given, else the tree's path, `work_folders.paths.page_path`
([work_folders.md](work_folders.md#paths)): the names of the folders from
the page's `folders[0]` up to W (W left out) joined with `/`, then its
`file_name`; a page without a `file_name` (a note page) is named by its
title. The disk's override is feedback's alone; the chat's tools and the
search name a file by `page_path`. `paths` is `{page id:
path}` from the vault's rows on this computer (`paths_of(vault.store.files(link_id))`:
each page at its PDF row's path, else its notes row's; tombstones, a row
with `was` or an in-place one, left out), what the files' writer passes so
the names are the disk's. Separators are `/`.

### An entry

```text
{"id":       "<thread root id>",
 "file":     "main.pdf",
 "format":   "pdf",                        # the document's format; "markdown" for a note page without one
 "page_id":  "…",
 "block_id": "…",                          # the block the thread hangs on
 "kind":     "passage" | "area" | "ink" | "region" | "note" | "file",
 "where":    {"page": 3, "quote": "…", "box": […], "rect": [x1, y1, x2, y2], "size": [w, h],
              "placed": "exact", "match": 0.82, "note": "…"},
 "source":   {"file": "sections/method.tex", "line": 118, "end": 120, "version": "current"} | null,
 "picture":  "img/<key>.jpg" | null,
 "before":   "img/<key>.jpg" | null,
 "state":    "open" | "resolved",
 "sent":     {"request_id": "…", "state": "sent" | "running" | "failed", "at": "…", "detail": "…",
              "state_by": "…"} | null,
 "link":     "<the thread's Gamma link>",
 "comments": [{"id", "by", "agent", "at", "body", "deleted", "agent_kind"?}]}
```

The keys come in that order, and a `where` key with no value is left out.

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
    note under a place;
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
- **`source`**: live (`version: "current"`) when the place is on a PDF
  page of the page's current file (`placed` is `exact`, `rough` or
  `estimate`) and the SyncTeX map answers on this computer ("SyncTeX");
  else the one recorded at the thread's open (`anchor.source`, `version:
  "commented"`); else null. `end` comes only when it differs from `line`. A
  region of a picture has none.
- **`picture`** and **`before`**: "The pictures". HTTP and MCP add
  `picture_url` and `before_url` after them (`entries.with_urls`).
- **`sent`**: the newest Send waiting on the thread (`comments._sent`: a
  `feedback_request_threads` row not answered whose request is `sent`,
  `running` or `failed`), `detail` and `state_by` (who set its state: a
  token's display name, the label of an agent on this computer, the
  requester's username) only when the request has them; null otherwise.
  The comment API's threads carry the same, `state_by` "" while it is
  sent.
- **`link`**: `?ws=<ws>&page=<page_id>&block=<block_id>&thread=<id>`, the
  address the Inbox opens, prefixed by `link_base` (MCP's absolute base;
  "" in the files and over HTTP, where the link is the query alone).
- **`comments`**: oldest first; `by` the author's username (the stored name
  once the account is gone), `agent` the token's display name or "", `at`
  the comment's `created_at`, `body` ("" with `deleted: true` for a
  tombstone). A comment an agent on this computer wrote
  ([local_agents.md](local_agents.md): its `agent_id` starts with
  `local:`, `comments.LOCAL_AGENT`) adds `agent_kind: "local"`, as the
  comment API's comment does; no other comment has the key, so every other
  entry and file stays byte-identical.

### The order

Files by name (case folded, then as it is); in a file the whole file's
threads first, then by `where.page`, `anchors.top`, the box's left edge
(else the rectangles'), the threads without a page after those with one: a
picture's regions by top and left edge, then the notes; ties by
`created_at`, then id.

### The document

```python
def entries(ws, conn, scope, *, state="open", paths=None, link_base="", sources=True, threads=None,
            regions=None) -> list[dict]
def document(ws, conn, scope, *, state="open", paths=None, owner="", link_base="", sources=True,
             regions=None) -> dict
def entry(ws, conn, thread_id, *, paths=None, link_base="", sources=True, regions=None) -> dict | None
```

`sources=False` leaves the live SyncTeX lookup out (the recorded source
stays): a picture's read needs none. `threads` keeps only those ids (a
request's entries). `regions`, a dict the caller passes, gains `{key:
(page id, Region)}` for every picture an entry names, so whoever draws them
(`pictures.cached`) knows what each key is. `entry` answers one thread in
any state, None when it is hidden (orphaned, gone), a reply's id, or not on
a live file of a work folder.

```text
{"format": "gamma-feedback-1", "workspace": "<ws>", "work_folder": {"id", "name"},
 "scope": {"kind", "id", "name"}, "owner": "<username>", "state": "open",
 "updated_at": "<the newest last_at among the entries>" | null,
 "count": n, "truncated": m (only when cut),
 "answer": {"reply": "POST /api/pages/{page_id}/comments {block_id, thread_id, body}",
            "resolve": "POST /api/comments/{id}/resolve {resolved: true}",
            "request_state": "POST /api/feedback/requests/{request_id}/state {state, detail}",
            "mcp": ["reply_feedback", "resolve_feedback"]},
 "entries": [...]}
```

`owner` is the username of the account the document is written for (the
vault's maker in the files; the requester's own account over HTTP and
MCP): how an agent tells the person's comments from other members'. Past
`MAX_ENTRIES` (500) the document is cut and `truncated` says how many more
there were. No per-round time is written, so an unchanged document is
byte-identical, which is what lets the files' writer leave an unchanged
file alone.

## The Markdown

`markdown.render(doc, *, pictures)` writes a document as the Markdown an
agent reads, `pictures` one of `files` (`![…](img/<key>.jpg)`, the files
beside `open.md`), `urls` (the entries' `picture_url` / `before_url`, the
routes) or `tool` (no image lines; a line `Picture:
read_feedback(thread_id="<id>")` instead). `render_entry(entry, *,
pictures, number=None)` writes one item, what the thread route's Markdown
and `read_feedback` give. Exactly:

```markdown
# Feedback on paper

2 open comments · owner tim · updated 2026-11-10 13:58

Each item is a comment thread on a file of this work folder. Answer it in Gamma: reply with what you
changed and where, resolve it when it is done (see "How to answer" at the end).

## main.pdf

### 1. Page 3 · “the loss is defined as”

- id: `0d7c…` · passage · placed exactly
- source: `sections/method.tex`, lines 118–120
- sent: waiting since 2026-11-10 13:59

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

## figures/fig1.pdf

### 3. The whole file

- id: `…` · file

> **tim** · …
> use the viridis palette.

---

## How to answer

- Reply: `POST /api/pages/{page_id}/comments` with `{"block_id", "thread_id", "body"}`, or `reply_feedback`.
- Resolve: `POST /api/comments/{id}/resolve` with `{"resolved": true}`, or `resolve_feedback`.
- The JSON form with every id is `open.json` beside this file.
```

The heading is the scope's name; the line under it counts the entries and
names the owner, the newest activity and, past the cut, `{n} more not
listed`. Item headings: `Page {n} · “{quote, at most 80 characters}”` (a
passage), `Page {n} · area`, `Page {n} · handwriting`, `Page {n} · note on
“{quote}”` (a note under a passage, `note on an area` and `note on
handwriting` under those), `Region · {w}×{h} px at ({x1}, {y1})` (a region
of a picture, by `where.rect`; `Note on a region · …` under one; `Region`
alone when the block is gone), `A note` (one without a place), `The whole
file`.
`placed` reads `placed exactly` / `placed roughly ({match})` / `placed by
estimate` / `not found in this build` / `not placed yet` / `drawn on an
earlier build`, and for a region `placed exactly` / `drawn on an earlier
version of the picture` / `the file is no longer a picture`; an item with a
`before` picture adds `the figure was rebuilt` (an area), `the PDF was
rebuilt` (a passage) or `the picture was rebuilt` (a thread on a picture's
page); a resolved item says so. The source line reads `lines a–b` when
`end` is there and `(as commented)` when its `version` is `commented`; a
`sent` line reads `waiting since`, `being worked on` or `failed`, with the
request's detail. The picture lines read `Page {n}, the comment's region`
(`now` when a `before` follows) and `Page {n}, the region as it was
commented on`, without the page for a region of a picture, and `The
picture` / `The picture as it was commented on` for a thread on a picture's
page. Bodies are quoted line by line with `> `, a body's own Markdown left
as it is; a tombstone reads `*(deleted)*`. A token's comment is signed
`**Claude Code** (tim's connection)`, one with `agent_kind: "local"`
`**Codex** (on tim's computer)`. The last line of "How to answer"
names the JSON form the reader can reach (`open.json`, the `GET
/api/feedback` query, or `read_feedback`). With no entries: the heading, `No
open comments.` (or `No resolved comments.`), and "How to answer". The
renderer has no i18n: the files are for agents, in English, the one text
Gamma writes untranslated ([i18n.md](i18n.md)); everything a person reads
in the app is translated.

## The pictures

`gamma/feedback/pictures.py`: the passage, area or handwriting a thread
hangs on, cut out of its PDF page as the page shows it; the region of a
picture a thread hangs on, cut out of the picture; the whole picture for a
thread on a picture's page. A passage, an area, ink, a region and a note
under one of those get pictures, and the page's thread when the page shows
a picture; a note alone and a PDF's whole file none.

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
  `feedback_pictures` by key, else renders and keeps, the oldest going first
  past `CACHE_BYTES` (48 MB; [user_db.md](user_db.md) "data.db"). In a work
  folder the file `img/<key>.jpg` is the cache too. Pictures are derived,
  never uploads.

## SyncTeX

Where in the sources a place on a work folder's PDF was typeset: an entry's
`source`. `gamma/feedback/synctex.py` is the reader (pure, stdlib only), and
`gamma/feedback/sources.py` maps a place to `{"file", "line", "end"?}` on
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
  engine, `tests/samples/synctex/`) and `tests/test_feedback_sources.py`.

## The commented version kept

A page whose document is read in place (`documents.model.in_place`: a work
folder's build or figure, an Opened file, a vault's PDF) has no stored copy
of its bytes, and the next build at its path takes them away. While such a
page has an open thread it keeps the bytes it shows stored
(`uploads/<hash><ext>`: a PDF's `.pdf`, a figure's `.png`), so the version
the thread was opened on stays readable: the relocation run places a PDF's
highlights again from it
([highlight_relocation.md](highlight_relocation.md)), and the reader draws
the `before` picture from it. `gamma/feedback/kept.py`:

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
  props)`: the page is a library file, or has an open thread) the incoming
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

## Requests and their states

Send is the person's "go": a request that an agent act on a scope's open
threads, with a line of instruction. `gamma/feedback/requests.py`, in the
workspace's pages.db (`db.FEEDBACK_SCHEMA`, additive: no migration step;
[user_db.md](user_db.md) "pages.db"):

| Table | Columns |
|---|---|
| `feedback_requests` | `id` (a block id's shape; the client may mint it, so a retried Send writes once), `rev` (the listing's cursor: the workspace's next number at the insert and at every change of state, under the write lock), `work_folder` (W), `scope` and `scope_kind` (`file` or `folder`: the page or folder id the person sent), `by` (the requester's account id) and `by_name`, `at`, `note` (at most 2000 characters), `state` (`sent`, `running`, `done`, `failed`), `state_at`, `state_by` (a token's display name, the label of an agent on this computer, or the requester's username), `detail` (`failed`: the reason; `running`: a line of progress; at most 500 characters), `via` (how it was made: `session` or `token`, `''` before migration step 58; not in the record) |
| `feedback_request_threads` | `request_id`, `thread_id`, `answered_at` (a reply or a resolve after the request, by a token or another account) |

A request record, as the API answers it: `{id, rev, work_folder: {id,
name}, scope: {kind, id, name}, by, at, note, state, state_at, state_by,
detail, threads: [{id, answered}]}`, `by` the username.

- **`create(ws, conn, scope, *, by, by_name, note, threads, request_id,
  via)`** → `{request, created}`, commits. `threads` omitted: every open
  thread in the scope; given: each must be an open thread there (400
  otherwise), at most 500. 400 "Nothing to send: no open comments here." for
  none; 413 past the note's length. A repeated `request_id` by the same
  account answers the stored request with `created` false; another
  account's id is a 409. `via` (`requests.VIA`) is how the Send was made,
  stored in the row's `via` column and not in the record: `session` (a
  person in Gamma) or `token` (an integration token), as the route says;
  `''` for a Send from before migration step 58. An agent on this computer
  takes a session's alone ([local_agents.md](local_agents.md#claiming-and-the-run)).
- **`listing(conn, *, after, scope, state, limit)`** → `{requests, cursor}`:
  `rev > after`, oldest first, `limit` 50 by default and at most 100;
  `scope` a W's or a file's id filters by `work_folder` or `scope`; `cursor`
  is the last `rev` given, else `after`. So a new request and a state change
  alike show past a cursor. `get(conn, id)` is one.
- **`set_state(ws, conn, id, state, *, detail, by_name, writer,
  only_from=())`**: `writer` is `agent` (an integration token of the
  `feedback` or `write` scope, or an agent on this computer) or
  `requester` (the session of `by`), and `TRANSITIONS` says who may make
  which change:

  | from \ to | running | done | failed |
  |---|---|---|---|
  | sent | agent | agent | agent; the requester (a withdraw) |
  | running | agent (a new `detail` line) | agent | agent; the requester |
  | failed | agent (a retry) | agent | — |
  | done | — | — | — |

  A refused change is a 409 "A {from} request cannot become {to}.". An
  agent's same state with the same detail again changes nothing (no
  `rev`), and so does a repeated withdraw; a requester's `failed` sets the
  detail `withdrawn` whatever they sent, and the withdraw is their only
  change (their `running` or `done`, repeated or not, is the 409). A
  write takes the next `rev`, sets `state_at`, `state_by` and `detail`.
  `only_from` names the states the writer expects the request in, checked
  under the lock: a stored state outside it is a 409 "The request is
  {state} now.". Every write of an agent on this computer passes
  `("running",)`, so a request withdrawn between two of them is never
  overwritten; an outside agent's writes pass none and are unchanged.
- **`claim(ws, conn, id, *, by_name, detail)`** → the request, or None:
  an agent on this computer takes a `sent` request
  ([local_agents.md](local_agents.md#claiming-and-the-run)), `running` with
  `detail`, `state_by` its label, the next `rev` and `state_at`, in one
  step under the write lock; a request that is no longer `sent` (taken,
  withdrawn, answered, gone) is rolled back and None, so of two takers, or
  a taker and a withdraw, one wins. Commits, then `_changed`.
- **After every write** (`_changed`): `collab.publish(ws, page, {t:
  "comments", block_id: "", thread_id: ""})` to each page whose threads are
  in the request (open tabs refetch their threads and Sent chips),
  `files.mark_folder(ws, W)`, and for a work folder of this computer whose
  vault may write now (not paused, not choosing its files)
  `files.write_request(link, id)` at once, best effort: the hot pass
  rewrites it on a failure. Then each of `change_listeners` hears
  `fn(ws, request_id)`, on the early-return path too, each in its own
  `try` that logs and never raises: `local_agents.runner.on_request`, which
  queues the Sends an agent on this computer answers and stops a run whose
  request was withdrawn, is the one listener.
- **Answered.** `answered(ws, event)`, the listener's part: a `reply` or a
  `resolve` sets `answered_at` on every row of the thread not answered whose
  request was made before the comment and whose requester is not the actor
  or whose event carries an `agent` (a token's write answers whoever it
  belongs to); then the work folder's files are due. A reopen answers
  nothing. So a thread shows as sent (`sent` on its entry and on the comment
  API's thread) until an agent or another member answers it, and a later
  Send waits on it again.
- **Travel.** Requests are not synced by the mirror, not published, not in
  a share export, not in a backup merge or a Gamma export (`copy_rows`
  mints fresh thread ids, so the join rows would name nothing); a restore
  in place copies them with the file; gone with the workspace. **The
  sweep**, `requests.sweep`, runs on the comments' timer, at startup and
  hourly (`app.sweep_comments`: after `comments.sweep`, whatever that one
  did, since the lifespan's chain of timers is at Python's limit of nested
  blocks) and, as that one does, remembers each workspace's file stamp and
  next expiry: it deletes `done` and `failed` requests 90 days after
  `state_at` (`KEEP_DAYS`) and the rows of threads that no longer exist.

### The request file

`requests.document_of(ws, conn, request_id)` is what `.gamma/feedback/requests/<id>.json`
holds, written by `files.write_request` and rewritten by every pass whose
request changed (a state change, an answer), under the same name, so a
watcher sees a create and then modifications:

```text
{"format": "gamma-feedback-request-1", "id", "rev", "workspace", "work_folder": {id, name},
 "scope": {kind, id, name}, "by", "at", "note", "state", "state_at", "state_by", "detail",
 "answer": {…as in the document…, "state": "POST /api/feedback/requests/<id>/state {state, detail}"},
 "entries": [the entries of its threads as they are now, in any state]}
```

`requests.entries_of` gives those entries (a thread gone since is left
out); `GET /api/feedback/requests/{id}` answers the same pair. The newest
100 request files are kept; older ones are deleted only when `done` or
`failed`.

## The files

For a work folder of this computer, the feedback is files in the
repository too, so an agent at the terminal reads them with no connection
to Gamma. `gamma/feedback/files.py` writes them below the vault's own
directory ([synced_vaults.md](synced_vaults.md), [work_folders.md](work_folders.md)):

```text
.gamma/feedback/
  .gitignore          "*\n": the repository's git never shows the churn
  open.json           document(W, state="open", owner=<the vault's maker>), 2-space JSON, UTF-8, "\n" at the end
  open.md             markdown.render(that, pictures="files")
  img/<key>.jpg       write-once by key
  requests/<id>.json  one per Send
```

- **When.** (a) Every whole round of a work-folder link (`folder_links._vault_round`,
  after `vault.sync.sync`, under the link's lock), when the round's status
  has no `stopped` or `offline` and the vault is not choosing its files;
  the summary goes into `status["feedback"]`; so a new work folder's first
  round makes the directory, with an empty document, before any thread.
  (b) The hot loop
  (`folder_links.hot_tick`) writes the work folders `files.due(HOT_S)`
  names, those marked at least that long ago: a link resting because it is
  paused, failed or choosing drops the mark (its next whole round writes),
  a held lock keeps it, otherwise `write(link)` under the lock, then
  `settle(ws, W, at)` with the monotonic time the pass began, so a mark
  newer than the pass stays. `due` keeps the newest mark of each work
  folder (a burst of writes is one pass, once the last is `HOT_S` old). So
  a comment, a reply, a resolve, a relocation or a Send reaches `open.json`
  within about 2–4 s.
- **What `write` does**, in order: `vault.disk.check_marker` (a copy of the
  directory or an empty mount never gets files); the paths from the vault's
  rows and the maker's username; `entries.document`, sources on, and the
  request files due this pass; `kept.repair` for the entries' pages; the
  pictures the entries name that `img/` lacks, drawn through
  `pictures.cached` and written with `vault.disk.write_own`, at most
  `PICTURES_PER_WRITE` (20) a write (a picture not written yet is null in
  this write's document, and the next pass fills it; one that cannot be
  drawn now, a build being written, waits for the next whole round);
  `.gitignore`, `open.json`, `open.md` and the request files, each written
  only when its bytes differ from the file's (a replace that keeps failing
  under a reader on Windows is left for the next pass, and is no error of
  the vault's); the request files past the newest `REQUEST_FILES` (100, by
  `rev`) deleted once their request is done, failed or gone; `img/` pruned
  of the pictures named by neither this `open.json`, the one it replaced
  nor any request file; Gamma's own stale temporary files in `feedback/`,
  `img/` and `requests/` swept (`vault.disk.sweep_own`; the vault's scan
  never enters `.gamma/`). The summary is `{"open": n}`, with `"left": m`
  when `m` pictures or files wait for the next pass (the cap, a reader's
  hold), which the hot pass reads to keep the mark; errors are caught into
  `{"open": n, "error": …}`, logged once per change of text, and settle the
  mark (the next whole round reports them again), so a copy of the
  directory is not retried every 2 s.
- **The request files.** Of the newest `REQUEST_FILES` requests, a pass
  reads a `sent` or `running` one again every time (an answer changes its
  entries) and a `done` or `failed` one only while its file is missing,
  its stored `rev` is behind the row's, or an entry of it placed on this
  build has no picture yet (a Send answered before a pass drew them, or
  past the cap). A Send also writes its file at once
  (`write_request`, `folder_links.try_holding`: only when the link's lock
  is free, else the hot pass writes it, the work folder being marked), with
  the pictures `img/` holds already and the rest null.
- **Removal.** `folder_links._remove_vault`, when the marker is ours, calls
  `files.remove(root)` before the marker is unlinked, and so does the
  release of a work folder whose push was lowered. The files are Gamma's
  and regenerable: deleted directly, never through the trash; a link or a
  junction planted inside the directory is unlinked, never entered. A paused
  vault writes nothing (the pause means "leave the directory alone"); the
  HTTP routes are unaffected.

## What keeps it current

`gamma/feedback/listeners.py`, registered by its `register()` from
`app.create_app`, so every server process has them:

- On every committed comment write (`comments.write_listeners`,
  [comments.md](comments.md) "The API"): `files.mark(ws, page_id)` (the
  page's work folder's files are due; cheap, no IO; nothing on a `sweep`
  event, which names no page), `requests.answered(ws, event)` and
  `kept.after_comment(ws, event)`, each in its own `try` with its module
  loaded when first asked for, so a missing piece or a failing one never
  stops the others, and none fails the write.
- On every committed op batch (`ops.commit_listeners`): `files.mark(ws,
  page_id)`, whoever wrote it, a vault included: a relocation, a trash or a
  restore and an orphaning delete move an entry. Nothing is read here;
  `files.due` resolves the marked pages to their work folders in one query
  per workspace.

Both parts mark only on a server that keeps folders on disk
(`config.folder_links_enabled`); the routes read live rows and need no
mark.

## The routes

`gamma/routers/feedback.py`, prefix `/api/feedback`. Every route is for the
workspace's members of any role and for integration tokens of any scope, a
token in its own workspace as on every route of the HTTP API; a share
visitor, signed in or not, gets 403 "feedback belongs to the workspace's
members". Every handler is a sync `def`: it reads the pages.db, and the
pictures are drawn, off the event loop. `FeedbackError` becomes the
`HTTPException` of its status.

| Method | Path | Answer |
|---|---|---|
| GET | `/api/feedback?scope=<id>&state=open\|resolved\|all&format=json\|md` | the document of a work folder, a directory or a file (`entries.document`, `owner` the requester's username, the entries with `picture_url` / `before_url`), or with `format=md` its Markdown (`text/markdown; charset=utf-8`, the pictures as their routes). On a server that runs agents (`local_agents.enabled`) the JSON adds `agent` and `agents_found` for the Feedback dialog (below the table); never the files' document. 400 for a library page or folder, another state or format; 404 for no such folder or live page |
| GET | `/api/feedback/threads/{thread_id}?format=json\|md` | `{entry}` in any state, or its Markdown (the file's heading, then the item); 404 for a thread hidden, gone, a reply's id or not on a work folder's file |
| GET | `/api/feedback/threads/{thread_id}/picture?at=now\|before&v=<key>` | `image/jpeg`, `Cache-Control: private, max-age=86400`; 404 when the entry has no such picture or it cannot be drawn now; 400 for another `at`. `v`, the picture's key, only busts a browser's cache |
| POST | `/api/feedback/requests` | `{id?, scope, note?, threads?}` → 201 `{request}` (200 for a repeated `id` of the same account, answered as stored). `require_ws(write=True)`: a member who may edit, or a `write` token; never a `feedback` or `read` token, a viewer or a share visitor (a Send starts work on the owner's computer). 400 for a scope outside a work folder, nothing to send, a thread that is no open one in the scope or a malformed id; 403 for a token's Send to a work folder whose vault here has an agent on this computer ("An agent on this computer takes Sends made in Gamma, not with a token."); 404 for no such scope; 409 for another account's id; 413 past the note's length |
| GET | `/api/feedback/requests?after=<rev>&scope=<id>&state=<s>&limit=<n>` | `{requests, cursor}` ("Requests and their states"); on a server that runs agents each record adds `local`, whether an agent on this computer holds it now; 400 for another state |
| GET | `/api/feedback/requests/{id}` | `{request, entries}`, the entries of its threads as they are now (with their picture routes): what its file holds; the record's `local` as in the listing; 404 |
| POST | `/api/feedback/requests/{id}/state` | `{state, detail?}` → `{request}`: an agent (a `feedback` or `write` token, in its own workspace) any change `TRANSITIONS` lets it make, the requester's session a withdraw (`failed`); 403 for a read token ("this token is read-only"), another member, a viewer or a share visitor; 404; 409 for a change refused, and for a token's change while an agent on this computer answers the request ("Codex on this computer is answering this request."); 413 past the detail's length; 423 while a delete holds the workspace |

`picture_url` is `/api/feedback/threads/{id}/picture?at=now&v=<key>`,
`before_url` the same with `at=before`.

`doc.agent`, on the desktop app's own server only (`_agent_of`): null when
the work folder has no agent on this computer; else `{name, label, ready,
problem, takes, why}`, `ready` and `problem` as last asked
(`local_agents.catalog.cached`: nothing runs, `ready` null while still
checking), `takes` whether this account's Send would reach the agent now
(the vault's maker, the vault writing its directory), and `why`, English,
when not: "Only the person who keeps this folder on this computer can send
to Codex." or "The work folder is paused on this computer: Codex starts
once it syncs again.". `doc.agents_found` says whether this computer has
either agent. Both are on this route alone: `open.json`, the request files
and MCP's `read_feedback` stay as they are
([local_agents.md](local_agents.md#the-routes)).

## Tokens and MCP

- **The `feedback` scope** (`integrations.SCOPES`): everything `read` may,
  plus a reply in and the resolve of a thread on a work folder's file, and
  setting a request's state. On the HTTP API it answers in its own
  `workspace_id` alone (every token's rule); on MCP in every workspace it was
  granted, like a read token ([mcp.md](mcp.md) "Permissions and
  credentials"). `auth.require_commenter(request, page_id, answer=)` lets
  it reply and resolve on a live file and refuses it a new thread, an edit,
  a delete and a reopen ([comments.md](comments.md) "Who may comment"). A
  `write` token answers through the comment API as before; a viewer may
  make `read` and `feedback` tokens (viewers comment), `write` stays the
  editors'. The pane's words: **Read and answer feedback**.
- **The stamp.** A comment written through any token stores the token's
  display name and id (`agent`, `agent_id`; `resolved_agent` on a resolve),
  shown as the comment's author with the person's account; a token edits
  and deletes only what it wrote, and a session of the account deletes its
  tokens' comments. A token's comment makes no one follow the page
  (`ops.UNFOLLOWED`'s rule), and the Inbox tells the person of it as of
  anyone else's write, each item naming the agent ([comments.md](comments.md)
  "The inbox"). A retry of a comment id is the same writer's: a session's
  id retried by a token is a 409.
- **MCP**: `list_feedback` and `read_feedback` are reading tools of the
  chat registry (`ai_tools.TOOLS`; [ai_tools.md](ai_tools.md)), reached on
  any connection and in the chat; `reply_feedback` and `resolve_feedback`
  (`gamma/feedback/agent.py`) are the adapter's own, listed for a `feedback`
  connection only and checked per call ([mcp.md](mcp.md) "Answering
  feedback"). `gamma_link(..., thread_id=)` writes the address the Inbox
  opens. A browser sign-in asks for the scope through the consent page's
  checkbox (`gamma:feedback`, [mcp.md](mcp.md) "Browser sign-in protocol").
- **The plugin skill** (`plugins/gamma/skills/gamma/SKILL.md` "Feedback on
  a work folder") tells the agent the loop: find the items (`.gamma/feedback/open.md`
  in the repository, or `list_feedback`), read each with its source line,
  change the sources and rebuild (Gamma builds nothing: it shows the new PDF
  and finds the comments again), reply with what changed and where and
  resolve what is done, leave what needs the person's decision open with a
  question, act on the owner's comments and ask about anyone else's, never
  answer its own replies, and never edit anything under `.gamma/`.

## The seam

On the desktop app's own server Gamma itself can be the connector: the
vault's maker turns on Codex or Claude Code for a work folder in the sync
panel, and from then on a Send of that folder made in Gamma by the maker is
claimed by this server (`requests.claim`, `sent` to `running` in one
step), worked by the agent in the vault's directory, answered with replies
signed by its label and set `done` or `failed`
([local_agents.md](local_agents.md)). While a folder has such an agent, a
token's Send to it is refused (403), and so is a token's change of a
request a run holds (409); the requests it has taken read `state_by`
`Codex` or `Claude Code`, and an outside agent leaves them alone.

Anywhere else, and for a folder with no agent on this computer, Gamma
starts no process. The owner builds the part that starts an agent (a
watcher, a timer, a shell hook), and what it finds here is:

1. **A request appearing.** On this computer, a work folder's
   `.gamma/feedback/requests/<id>.json` ("The request file", `format:
   "gamma-feedback-request-1"`): a Send writes it at once, and every
   change of the request rewrites the same file with its `rev` moved, so a
   watcher sees a create, then modifications, and a `rev` it has seen is
   nothing new. On any server, `GET /api/feedback/requests?after=<cursor>`
   with a `feedback` token: the requests made or changed since the cursor,
   oldest first, with the `cursor` to pass back next time (`state=sent`
   keeps the new ones); the HTTP API reads the token's own workspace, so
   one token per workspace polled.
2. **What to do.** The request record ("Requests and their states"):
   `note`, the person's line of instruction; `scope`, the file or folder
   they sent; `threads`, each with `answered`; `by`, their username. Its
   entries come in two forms: JSON (the request file's `entries`, their
   pictures named `../img/<key>.jpg`; `GET /api/feedback/requests/{id}`;
   the scope's `open.json` or `GET /api/feedback?scope=`) and Markdown
   (`open.md`, `GET /api/feedback?scope=<W>&format=md`,
   `GET /api/feedback/threads/{id}?format=md`), each entry with its id,
   file, place, source line, pictures and comments ("An entry"). The
   document's `owner` says whose comments are the requests, and its
   `answer` names the routes below.
3. **Saying it started.** `POST /api/feedback/requests/{id}/state
   {state: "running"}` as the agent: an integration token of the
   `feedback` or `write` scope, in its own workspace; again with a
   `detail` line as it goes. Gamma shows it beside the request ("The agent
   is working").
4. **Answering.** Through the comment API with the same token, each entry
   carrying its `page_id` and `block_id`: a reply is
   `POST /api/pages/{page_id}/comments {block_id, thread_id, body}`, a
   resolve `POST /api/comments/{id}/resolve {resolved: true}`; or over MCP
   with `reply_feedback` / `resolve_feedback` on a `feedback` connection.
   Each answer is signed with the token's name, reaches the person's
   Inbox, and marks the thread answered in every request waiting on it
   (`threads[].answered`, the entry's `sent` gone), which rewrites the
   request file.
5. **Saying it is done.** `POST …/state {state: "done"}`, or
   `{state: "failed", detail: <why>}`; a failed request may be set
   `running` again (a retry) and then `done`; `done` is final. A request
   the person withdrew reads `failed` with the detail `withdrawn`, set by
   the requester's session (`state_by` their name): the connector stops on
   it and may not change it. A request `running`, `done` or `failed` that
   the connector did not set is someone else's (its `state_by` names who
   has it): it leaves it alone. The table of changes and who makes each is
   `TRANSITIONS`; a change refused is a 409. Gamma shows the state beside
   the request, with the detail.

## In the app

The client's side is the comments' ([comments.md](comments.md) "In the
app", [ui-design.md](ui-design.md)): an ink group takes a thread (Comment
in the lasso's menu, a card in the PDF margin level with the stroke; a group
with a thread takes no new strokes, is never deleted by the saver and
refuses a merge, [handwriting.md](handwriting.md)); a thread's card shows
its agent's comments under the token's name, a Sent chip while a request
waits on it, and whether its passage moved or was not found in the new
build; the Feedback dialog (`frontend/src/feedback/`: `FeedbackDialog.jsx`
over the pure `feedbackView.js`, `tests/feedbackView.test.mjs`; its state
a module store, `useFeedback.js`, that each opener calls and
`FeedbackHost.jsx` renders; the words in `feedbackText.js`), opened from
the work folder's list view, the Comments window's header on a file and
the vault chip's card, lists a scope's entries with their thumbnails and
sends them with a line of instruction, the requests below with their
states and a Withdraw; while open it reads the scope's document and the
work folder's requests again every `POLL_MS` (5 s), so an agent's answers
show as they come. The Inbox names the agent on its items. The user
guide's "Comments and inbox" says it for people.

## Known gaps

- A library page's threads are not feedback (above).
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
  rebuild; an Inbox item when a request is done; a palette command for
  Feedback.
- Comments made on another server (the NAS, a phone's clone) never reach
  the desktop's files or its agent: comments do not travel. The routes of
  the server that holds them serve them.
- The comment API's thread carries `made_on` (the anchor's `doc_id`), not
  the anchor; the reader reads the stored anchors itself.
- Every build shown while a thread is open is stored: a thesis rebuilt
  fifty times in an afternoon stores fifty copies, released a day after
  they are replaced and their highlights placed.
- A picture past Pillow's decompression bound (`Image.MAX_IMAGE_PIXELS`,
  about 89 Mpx) or past `STORED_MAX_BYTES` draws no picture, and there is no
  setting for either. The chat's region picture of a picture page shows
  nothing until the image branch of `/api/ai/page-image` answers.

## Tests

- `tests/test_feedback_anchors.py`: the anchor of each kind, a region's and
  the extension held.
- `tests/test_feedback_entries.py`: every kind's `where` and `placed`, a
  picture's regions and the whole picture through a rebuild, the file's
  name from the vault's rows and from the tree, the order, `sent`, a
  tombstone, the cut, the scopes and their refusals, the states, a root from
  before step 54, the source live or as commented, the pictures' keys; the
  Markdown of a fixed document byte for byte, the empty form and the
  picture modes.
- `tests/test_ai_image_region.py`: the picture a page shows as the server
  draws it (`ai_pictures.image_region`) and the page-image route of a
  picture page; `tests/test_feedback_images.py`, step 3's gate: a work
  folder's PNG, a region and a thread on it and on the page, their
  pictures in `.gamma/feedback/`, and a rebuild of the picture.
- `tests/test_feedback_routes.py`: JSON and Markdown, one thread, the
  picture route, who reads.
- `tests/test_feedback_requests.py`: who may send, a retry, "Nothing to
  send", every change of state by each writer, the cursor, `answered_at`,
  the room publish, the listeners, the sweep, `claim`, `only_from` and the
  `change_listeners`. An agent on this computer has its own suites,
  `tests/test_local_agents_*.py` ([local_agents.md](local_agents.md#tests)).
- `tests/test_feedback_pictures.py`, `test_feedback_kept.py`,
  `test_synctex.py`, `test_feedback_sources.py`, `test_feedback_files.py`,
  `test_feedback_tools.py`, and the gate `test_feedback_loop.py` (a work
  folder with a paper, two comments, a Send, a feedback token's state and
  answers, a rebuild, the Inbox, `open.json` empty at the end).
- The browser group `feedback` (`frontend/tests/e2e/scenarios/feedback.mjs`).
