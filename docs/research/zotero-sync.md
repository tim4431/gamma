# Zotero's cloud as a synced remote of a Gamma workspace (October 2026)

Can a Zotero library on zotero.org stand to a Gamma workspace as a clone's
origin does to the clone ([dev/mirror.md](../dev/mirror.md)): every change
made in Gamma goes to Zotero, every change made in Zotero's reader or on
another computer arrives in Gamma, so that Gamma is the PDF reader of a
Zotero library? And is that the logic the Zotero import and export already
have? This note keeps what Zotero's Web API offers, what an annotation item
is exactly, what a run against a real library confirmed, how the three
pieces Gamma already has (the RDF round trip on `dev`, the `zotero-cloud`
branch, the mirror) relate to it, and the plan. The mechanics of what
exists are in [dev/import_export.md](../dev/import_export.md) and
[dev/mirror.md](../dev/mirror.md); the branch's own reasoning is in its
`docs/research/zotero-web-api.md` (not on `dev`). This note does not
repeat them.

## How it was found

Zotero's documentation of the Web API v3 was read: basics, syncing, write
requests, file uploads, OAuth, full-text content, the streaming API, the
local API, "Zotero 10 for developers" and the changelog. Because the
documentation says nothing about annotation items, the code that serves
api.zotero.org was read instead: the dataserver's `model/Item.inc.php` and
`model/Items.inc.php` (AGPL, `zotero/dataserver`), which validate and store
every item the API takes. The client's rules came from `zotero/zotero`
(`xpcom/annotations.js`, `xpcom/data/item.js`, `xpcom/streamer.js`,
`xpcom/sync/syncAPIClient.js`, `syncEngine.js`, `syncLocal.js`) and the
reader's `src/common/types.ts` and `src/pdf/selection.js` (the sort index).
Three forum threads on programmatic annotations, two pyzotero and
zotero-mcp issues and two zotero-mcp pull requests were read for what
others found against running Zoteros. On 28 October 2026 everything that
matters was then run against a real library with the developer's own API
key (full access, 46 items, 16 reader annotations of all six kinds, no
groups): the script created a test collection, item and PDF attachment,
every annotation kind, probed the validation, versions, trash and delete,
the deltas, the file protocol and the streaming socket. Its artefacts stay
in the library under the collection "Gamma sync test" for a look in a
reader. On Gamma's side: `dev`'s `zotero_import.py`, `zotero_export.py`,
`zotero_notes.py`, `highlights.py`, `ink.py`, `pdf_export.py`,
`sync_engine.py`, `routers/mirrors.py` and the mirror's frontend, and the
`zotero-cloud` branch's modules, tests and doc sections. The desktop Zotero
installed on the developer's machine (9.0.6, local API on) is signed in to
no sync account, so no desktop rendering was checked; see "Still open".

## Short answer

1. **Not the same logic.** The RDF import and export exchange *files*: a
   zip of RDF/XML plus PDF copies, with Zotero's annotations burned into
   the copies as PDF annotations and read back out of them. The Web API is
   a *database*: items with keys and versions, where a reader annotation is
   an item of its own under the PDF attachment, with its rectangles as JSON,
   and where a client asks "what changed since version N" and writes "if
   nothing changed since N". Only the field mapping, the note converter
   (`zotero_notes.py`) and the rectangle conversions (`pdf_export.py`)
   carry over. The `zotero-cloud` branch built the Web API side as far as a
   reviewed import and a reviewed "Send to Zotero" go; what it left out
   (annotation items, a sync that runs by itself, deletions, incremental
   reads) is exactly what a remote workspace adds.
2. **Annotation items can be written, and now that is checked.** The
   branch did not write them, because two forum threads reported items
   "accepted but never shown". api.zotero.org validated and stored all six
   kinds from the test key, with the server's messages matching the
   dataserver code line for line; the shapes it stored are the shapes the
   reader itself had written into the same library two days earlier; and
   Zotero's reader, opened on the test PDF, drew every one of them, comments,
   tags and author name included. The forum reports trace to the client's
   SQLite and internals on Zotero 9, and to a missing sort index.
3. **Feasible, as a second engine beside the mirror, not as a new remote of
   it.** The mirror's protocol is Gamma-only: page trees, op batches, a
   change feed by `seq`, a three-way merge per block, files by content
   hash. Zotero's is item-level: one version per object, no op log, no
   merge on the server (a stale write is refused whole with 412). What
   transfers is the *shape* around the engine (a registry row, a cadence,
   a log, conflict rows, the header's sync pill and conflict card) and the
   branch's pull and push, which become one round. Zotero itself is a sync
   client of zotero.org with this very protocol, so Gamma would be a third
   client, like a second desktop.
4. **"Mimicking the desktop" is using the key.** The desktop client is an
   ordinary client of the same API: `Zotero-API-Key`, `Zotero-API-Version:
   3`, a `Zotero-Schema-Version` header, `If-Unmodified-Since-Version` on
   uploads, 100 objects a read and 50 a write (`syncAPIClient.js`). There
   is no second, private protocol for annotations.

## What the Web API offers

- **Keys.** `Zotero-API-Key` plus `Zotero-API-Version: 3`; a key never
  expires; `GET /keys/current` names its user and access (library,
  notes, write, files, groups). OAuth 1.0a only, whose access token *is*
  the key; the branch has both the OAuth steps and a pasted key.
- **Libraries.** `users/<id>` and `groups/<id>` prefixes; `/users/<id>/groups`
  lists the groups the key reaches.
- **Reading.** `items`, `items/top`, `items/<key>`, `items/<key>/children`,
  `collections`, `tags`; at most 100 objects a page (`limit`, `start`,
  `Link` headers, `Total-Results`); `itemType=annotation` lists a library's
  annotations; `itemType=-attachment` and other negations *exclude*
  annotations too (checked: the count dropped by the annotations as well),
  so they must be asked for by name or through `children`. Trashed items
  are left out unless `includeTrashed=1`, and come with `deleted: 1`.
- **Versions.** Every multi-object answer carries `Last-Modified-Version`,
  the library's version; every object carries `version`.
  `If-Modified-Since-Version` turns an unchanged listing into a 304 (one
  cheap request says "nothing moved"); `?since=<version>` lists what
  changed, `?format=versions` as `{key: version}` only;
  `/deleted?since=<version>` lists the keys deleted since (items,
  collections, searches, tags, settings). This is the whole delta protocol;
  the documented algorithm (fetch versions, fetch changed objects by key,
  apply, fetch deletions, restart when the library's version moved
  mid-walk, then upload) is what the desktop client runs
  (`syncEngine.js`, `DOWNLOAD_RESULT_RESTART`).
- **Writing.** `POST <prefix>/items` with an array of up to 50 objects;
  `PATCH items/<key>` with the changed fields; `PUT` whole. A write of an
  existing object must carry its `version` or `If-Unmodified-Since-Version`
  (428 "Either If-Unmodified-Since-Version or object version property must
  be provided for key-based writes"), and fails with 412 when the object,
  or for multi-object requests the library, moved since; the multi-object
  answer sorts objects into `successful`, `unchanged` and `failed` by
  index, so one stale object does not fail the batch. A client may choose
  keys (8 characters of `23456789ABCDEFGHIJKLMNPQRSTUVWXYZ`) and create
  with `version: 0`, which fails that object with 412 "expected 0, found
  N" when the key exists: an idempotent create. `dateAdded` and
  `dateModified` sent on a create are kept as sent. `DELETE
  items?itemKey=…` removes up to 50 for good; `deleted: true` in a PATCH
  moves to the trash instead. Child items (notes, attachments,
  annotations) name their parent in `parentItem`; a parent created in the
  same request comes first with a chosen key (checked).
- **Files.** `GET items/<key>/file` answers a 302 to a pre-signed URL on
  `zoterofilestorage.s3.us-east-1.amazonaws.com` (`/file/view` to
  `files.zotero.net`), with `Zotero-File-Compressed: No` on a plain file;
  the body's MD5 is the attachment's `md5`. Upload is three steps
  (authorize with `md5`, `filename`, `filesize`, `mtime` in ms and
  `If-None-Match: *` or `If-Match: <md5>`; post `prefix` + bytes + `suffix`
  as the given multipart body to the URL, 201; register `upload=<key>`,
  204), or `{"exists": 1}` when the server already has the bytes. 413 past
  the owner's quota. Only Zotero Storage is served: a WebDAV user's files
  and linked files are not reachable through the API at all. A snapshot of
  several files comes zipped (`Zotero-File-Compressed: Yes`).
- **Full text.** `GET/PUT items/<key>/fulltext` (`content`, `indexedPages`,
  `totalPages`), `GET fulltext?since=` for the changed ones. Gamma has its
  own index; writing Zotero's would make Zotero's search see Gamma's PDFs
  before the desktop indexes them.
- **Push notifications.** `wss://stream.zotero.org`: a `createSubscriptions`
  message with `apiKey` and optional `topics` (`/users/<id>`,
  `/groups/<id>`); the answer lists the key's topics (`/users/<id>` and
  `/users/<id>/publications` for this key); the server then sends
  `{"event": "topicUpdated", "topic", "version"}` on every change,
  `topicAdded`/`topicRemoved` when the key gains or loses a library, and
  `retry` on connect. No data travels: the client runs its sync on the
  event. Checked: a PATCH through the API raised `topicUpdated` with the
  new version 3 seconds later. The desktop client listens to the same
  stream (`xpcom/streamer.js`), so a change Gamma writes is on an online
  desktop's screen seconds later.
- **Limits.** `Backoff: <s>` on any answer (then wait), `Retry-After` on 429
  and 503, about four requests in flight; the branch's client keeps all of
  this per key.
- **The local API** (Zotero 10, released 17 August 2026; 10.0.6 on 7
  October): `http://localhost:23119/api/` serves the same endpoints over
  the desktop's own database, reads unauthenticated, writes (items,
  collections, searches, full text, file uploads under 4 GB for
  `imported_file`/`imported_url`) behind a key the user grants in a dialog
  (`POST /api/local/authorize`, single-use unless "Always allow"). Every
  answer carries `Zotero-Server-ID`, which a write must send back (428
  without); object versions are the instance's own, unrelated to
  zotero.org's; a file download is a 302 to a `file://` URL. 10.0.5 fixed
  writes with client-chosen keys. pyzotero's "local API lists no
  annotations" issue was closed in September 2026 as not reproducible on
  current Zotero. Only a Gamma on the same machine (the desktop sidecar,
  behind a deliberate loopback exception in `net_guard`) can reach it; it
  is the route for WebDAV and linked-file users, whose PDFs the Web API
  never serves.

## What an annotation item is

An `annotation` is a child item of a *file* attachment (`linkMode` not
`linked_url`); for `image`, `ink` and `text` the attachment must be a PDF,
for `highlight`, `underline` and `note` a PDF, an EPUB or an HTML snapshot.
Its JSON (the client's `toJSON`, the server's the same; this one was
accepted and read back):

```json
{"itemType": "annotation", "parentItem": "AKM9VFIH",
 "annotationType": "highlight", "annotationAuthorName": "",
 "annotationText": "quantum computation", "annotationComment": "gamma highlight",
 "annotationColor": "#ffd400", "annotationPageLabel": "1",
 "annotationSortIndex": "00000|000033|00108",
 "annotationPosition": "{\"pageIndex\":0,\"rects\":[[154.47,663.81,289.10,683.05]]}",
 "tags": [{"tag": "gamma-ann-tag"}], "relations": {}}
```

- `annotationType`: `highlight`, `underline`, `note`, `image`, `ink`,
  `text`; the server refuses any change of it, even highlight to underline
  (400 "Cannot change existing annotationType"), which the client allows
  locally. A missing type is a 500 "An error occurred", so it is always
  sent. The published schema lists the type with *no* fields and
  `items/new?itemType=annotation` answers 400 "annotationType required",
  so the JSON is built by hand, as above.
- `annotationText` only for highlight and underline (400 "'annotationText'
  can only be set for highlight and underline annotations"), cut to 7,500
  characters by the server. `annotationComment` is any string; the reader
  strips its markup to `<i> <b> <sub> <sup>`.
- `annotationColor`: `#` and six *lowercase* hex digits (`#FFD400` is a
  400 "annotationColor must be a hex color"); default `#ffd400`. The
  reader draws any colour; its picker offers eight (`#ffd400 #ff6666
  #5fb236 #2ea8e5 #a28ae5 #e56eee #f19837 #aaaaaa`).
- `annotationPageLabel`: at most 50 characters (400 "Annotation page label
  is too long for attachment"); how the readers pick it is
  [below](#how-the-reader-picks-a-page-label-october-2026).
- `annotationSortIndex`, for a PDF parent: `^\d{5}\|\d{6}\|\d{5}$` (400
  "Invalid sortIndex"), computed by the reader (`selection.js
  getSortIndex`) as the page index; the index, in the page's character
  list, of the character nearest (by the reader's `rectsDist`) the rect
  the reader's own sort puts first (the one with the greatest `r[2]`, its
  x2, which the reader's code calls *top-most*: not the greatest `y2`, as
  this note first had it; for ink and image the bounding box); and the
  page height minus that rect's top, floored, at least 0. The server
  checks only the shape; a wrong offset mis-sorts the sidebar
  (zotero-mcp's first version wrote y and x there and listed a page's
  highlights bottom-up), and an empty one is what the forum reports of
  invisible annotations have in common. Gamma can compute the offset: the
  embedded-annotation importer already reads each page's glyphs with
  their rectangles. Calibrated once built: the offset as the index of the
  nearest MuPDF character, whitespace and boxless characters skipped and
  a ligature MuPDF spells out as several characters folded into one (as
  pdf.js keeps it), matched what Zotero's reader wrote, exactly, on 6 of
  6 of the annotations the reader itself had made in the developer's
  library, and so did the top; without the ligature fold it was 4 of 6,
  two ink drawings 9 and 15 characters off.
- `annotationPosition` is a JSON *string*, at most 65,535 bytes on the
  server (400 "Annotation position is too long for attachment"); the
  client splits an annotation whose position would pass 65,000 into
  several (`splitAnnotationJSON`), so a long drawing goes as several items.
  Unknown keys inside it are stored and served back verbatim (checked with
  `width`, `height` and a nested object), though nothing says the reader
  keeps them when it re-saves a moved annotation. Coordinates are PDF user
  space of the page: points, origin bottom left, before the page's
  rotation, each rect `[x1, y1, x2, y2]` with `x1 < x2`, `y1 < y2`. The
  shapes (the reader's `PDFPosition`, each seen in this library as the
  reader wrote it):

  | kind | position | the rest |
  |---|---|---|
  | highlight, underline | `{pageIndex, rects: [[…], …]}`, one rect per line, `nextPageRects` for a passage that runs onto the next page | `annotationText` the passage, `annotationComment` |
  | note | `{pageIndex, rects: [[x, y, x+22, y+22]]}` | `annotationComment` the note |
  | image | `{pageIndex, rects: [[…]]}` | the picture is *not* in the item: each desktop renders and caches it from the PDF when the PDF is next opened (forum answers by staff), so there is nothing to upload |
  | ink | `{pageIndex, width, paths: [[x1, y1, x2, y2, …], …]}` | one colour and one width per item; no pressure, no timing |
  | text | `{pageIndex, fontSize, rotation, rects: [[…]]}` | `annotationComment` the text, `annotationColor` its colour |

- `annotationAuthorName`: empty for the key's own user; a value is stored
  as sent and the reader shows that name on the annotation. `readOnly` in
  the reader is "external (embedded in the PDF) or created by another user
  of a group", judged by `createdByUserID`, which the server sets from the
  key, never from the JSON: in a group library, members cannot edit each
  other's annotations and admins can only delete them (Zotero's position
  since 2022, unchanged). Everything Gamma writes with one key is therefore
  that user's in Zotero's eyes, editable by them, and labelled with a
  collaborator's name at most.
- Tags: an annotation may carry tags (stored, checked); Gamma has no place
  on a block for them. `collections` do not apply to child items.
- Deleting an annotation in the reader moves it to the trash: a pull sees
  `deleted: 1` with `includeTrashed=1` and nothing without it, and
  `/deleted` names only what was removed for good (checked both). Gamma
  trashes the same way (a PATCH with `deleted: true`), never `DELETE`.
- External annotations (embedded in the PDF, `annotationIsExternal`) are
  never synced by the client (`syncLocal.js`), so Gamma's highlights burned
  into an uploaded PDF, the branch's way, never become items.

### How the reader picks a page label (October 2026)

Read from Zotero 9.0.6 as installed (`resource/reader/reader.js`,
`resource/reader/pdf/build/pdf.worker.mjs`, pdf.js 5.4.0), the reader's
master and the web library's deployed `reader.js`, zotero/pdf.js
(`master`, `10.0`), the document worker's master and mozilla/pdf.js.

- An annotation's label is the reader's `_getPageLabel(pageIndex, true)`:
  `pageLabels[pageIndex] || pageIndex + 1`, or, when the last non-read-only
  annotation on a page at or before it has a numeric label (or a range like
  "12-13") and the PDF's labels advance with the page index there, that
  annotation's offset carried on.
- Zotero 9's desktop reader fills `pageLabels` from its pdf.js fork's
  `getPageLabels2` (`src/core/module/page-label.js`, `predictPageLabels`):
  the catalog's labels (pdf.js `catalog.pageLabels`) when they cover every
  page and a page number printed on one of the first 25 pages equals the
  catalog's label of its page, or none is arabic, or one is roman; else
  the first printed arabic number carried over every page ("-" below 1);
  else page numbers. The reader's master does the same through the
  document worker's `addPageLabels` (`src/pdf/structure/page-label.js`)
  while the structured document is on: the catalog's labels when they
  cover every page, else printed arabic numbers found on at least 30% of
  the pages (and roman ones on at least five), "-" elsewhere, else page
  numbers.
- The web library, and the reader's master otherwise, take pdf.js
  `getPageLabels()` (`pdf-document-data.mjs` `_loadNativePageLabels`),
  page numbers when it gives null or the wrong length.
- pdf.js reads the tree breadth first (`NumberTree.getAll`; a node with
  `/Kids` for its kids alone, a kid twice an error; Zotero 9's bundled
  pdf.js 5.4.0, like the viewer's 4.4.168, takes an inline kid for a
  repeat of an inline root or of another inline kid, so such a tree gives
  null there), each range's `/Type` (PageLabel), `/S` (D, R, r, A, a:
  letters repeat, 27 is AA, 53 AAA; none or the empty name: the prefix
  alone), `/P` (`stringToPDFString`: UTF-16BE or UTF-16LE after a byte
  order mark, an odd last byte dropped, UTF-8 after its mark, else
  PDFDocEncoding, ESC language codes left out) and `/St` (an integer of
  at least 1); any broken rule gives null.
- PyMuPDF 1.28.2's `page.get_label` and `get_page_labels` give a prefix in
  the syntax MuPDF prints it in, not decoded: `<FEFF0031>` for a UTF-16
  "1", hex or a literal `(\376\377\0001)` alike; `Ann\351e-` for the
  PDFDocEncoding "Année-"; and 53 in the letter style as BA.
  `pdf_text.read_page_labels` reads labels as pdf.js does, and a label a
  create sent so is corrected once
  ([dev/zotero_sync.md](../dev/zotero_sync.md#kinds-and-positions)).

## Gamma's blocks against Zotero's kinds

Both directions exist in code for the file path, and the branch reads every
kind from the API; the row says what a *write* of items would reuse and
lose.

| Gamma | Zotero | out (reuse) | lost out | lost in |
|---|---|---|---|---|
| highlight: `pdf_position` (top-left frame, rotation applied), `quote`, `color`, child notes | `highlight`: rects, `annotationText`, `annotationComment` | `pdf_export._highlight_annotation`'s rect → user-space conversion; first child note as the comment (as the RDF copy's `/Contents`) | notes below the first, Markdown in the comment | underline becomes a highlight; tags |
| area highlight (`area: true`) | `image`: rects | the same conversion | nothing | nothing |
| ink group: `gamma-ink` file, strokes with pressure, time, per-stroke colour, size, opacity | `ink`: paths, one `width`, one colour | `ink.ink_buckets` (one item per tool/colour/size/opacity bucket, as one `/Ink` per bucket); `from_pdf_ink` the other way | pressure, timing, opacity, pencil grain; a group is several items, re-keyed per bucket | strokes come back as flat polylines |
| text box: Markdown, `size`, `color`, `bg` | `text`: rects, `fontSize`, `rotation`, comment | `text_box` → rect with `_turned_box`'s inverse | Markdown, fill, a turned box (Gamma's cannot turn) | a turned text stands upright |
| text box (the import's choice) | `note`: a 22 pt square | the box's corner | the box's size and text styling | a sticky note is a box, not a note on the page |
| page record (`meta`), labels, folders | item fields, tags, collections | `zotero_fields`, the branch's three-way field merge | what the branch's notes say | what the branch's notes say |
| note tree | note HTML | `zotero_notes` | lists and structure, per the branch's `lossy` rule | the same |
| a replaced PDF with relocated highlights | a new file under the same attachment, or a new attachment | the three-step upload (`If-Match` the old MD5) | Zotero keeps its own annotations where they were: it has no relocation | nothing |

The annotation key both sides agree on: for an annotation that came from
Zotero, Zotero's key (`properties.zotero_annotation`, which the import
keeps); for one Gamma made, a key derived from the block id with
`zotero_web.object_key`, created with `version: 0`, so a round that stops
after Zotero saved it finds it next time instead of making a second. A
bucket of an ink group derives its key from the block id and the bucket's
index, as `pdf_export.zotero_annot_key` does for the `/NM`.

## What exists, and how far each is from a remote

| | RDF round trip (`dev`) | `zotero-cloud` branch | mirror (`dev`) | a Zotero remote |
|---|---|---|---|---|
| carrier | a zip the person moves | the Web API, one key per account | Gamma's own HTTP API, a write token | the Web API, the account's key |
| annotations | inside PDF copies, as PDF annotations | read as items; written only inside uploaded PDFs | blocks, by id | items both ways |
| what changed | nothing: whole library each time, merged by key | per field, three ways against a snapshot the page keeps | per block, three ways against the tree the copy kept | per field for records, per note, per annotation; `since=` for Zotero's side, the change feed for Gamma's |
| conflicts | none (the import fills gaps) | kept for the person's review; "Use mine" | rows, never blocking; an edit beats a delete | rows, never blocking, the same rule |
| deletions | never | never | pages and blocks both ways | annotations, notes and pages to the trash both ways |
| who starts | the person | the person, with a review | a cadence, a tab's return, an edit | the same, plus the stream |
| files | in the zip | download on demand, upload what Zotero lacks | by hash both ways | the branch's |
| identity | `zotero_key` | `zotero_item {library, key, version, attachment, home, synced}`, `zotero_note_sync {version, hash, lossy}` | `mirrors` row, `sync_pages.base` | a `zotero_links` row and a `zotero_objects` table (below) |

So the branch is most of the remote already: its pull and push are the two
halves of a round, each written to be re-run from fresh state, and each
keeps the snapshot a three-way merge needs. What it lacks is the loop and
the annotations, and what it has too much of is a reviewed one-shot import
and push of 16,000 lines, whose state (`zotero_item`, `zotero-libraries`)
is spread over page properties and prefs.

## Verified against the real library (28 October 2026)

| Checked | Result |
|---|---|
| `GET /keys/current` | the user id, name and access, groups included; `Zotero-Schema-Version: 42` on every answer |
| create a collection, an item and its attachment with chosen keys and `version: 0`, parent and child in one POST | all created; the item kept the `dateAdded` and `dateModified` sent |
| the same create again | `failed` with 412 "expected 0, found 260" |
| the three-step upload of a 1.5 KB PDF | authorize → S3 multipart (201) → register (204); `md5` and `mtime` set on the attachment |
| 7 annotations: the six kinds plus one with `annotationAuthorName`, tags and a `dateModified` | all stored and read back unchanged |
| colour `#FFD400`, sort index `0\|1\|2`, text on an image, a 14,000-number ink path, a regular item as parent, no type, a 60-character label, extra keys in the position | 400 with the dataserver's messages for the first five and the label; 500 for no type; the extra keys stored |
| `children`, `itemType=annotation&since=&format=versions`, `since=&includeTrashed=1`, `If-Modified-Since-Version` | as documented; the 304 answers with no body |
| `itemType=-attachment` | excludes annotations as well (22 of 56) |
| PATCH with the version, with a stale version, with none | 204; 412 "expected 263, found 271"; 428 |
| a multi-object update with one current and one stale object | the current one landed (`successful`), the stale one is in `failed` with 412 |
| trash (`deleted: true`), then DELETE another | the trashed one is listed only with `includeTrashed=1`, with `deleted: 1`; `/deleted` names only the DELETEd key |
| file download of the test PDF and of a 2.9 MB paper | 302 to S3, bodies' MD5 match; `/file/view` goes to files.zotero.net |
| the streaming socket | `subscriptionsCreated` with the key's two topics; `topicUpdated` 3 s after a PATCH |
| the desktop here | Zotero 9.0.6, local API on, signed in to no account: no rendering check possible here |
| the reader, opened by the developer on the test PDF | every live item drawn: both highlights with their comments, the tag `gamma-ann-tag` in the sidebar and its tag filter, the highlight with extra position keys, the authored highlight with "Collaborator Name" beside its page label, the underline, the image (its sidebar picture rendered from the PDF, the ink inside it included), the ink; the trashed note and the deleted text annotation absent |
| the sidebar's order | the ink listed first: the test gave it offset 0 in its sort index. The offset is the reading-order key for every kind, so it must be computed for ink and image too (from their bounding box), not only for text |

The library already held two annotations made through the API on 25
October (a note and an image with Python-formatted positions and extra
`width`/`height` keys): earlier experiments, which the reader's own
annotations of 26 October sit beside.

## The plan

Built, with the differences listed under [Outcome](#outcome-october-2026).

### Decisions

- **One engine for both directions, no reviewed one-shots.** A *Zotero
  link* is a workspace kept in step with one Zotero library, as a clone is
  with its origin: it is made in Settings → Workspaces → Clones, shows the
  same pill, log, conflicts, cadence, direction, Sync now, force and
  detach, and runs on the same clock. The link's first round is the
  import; its rounds are the push. The branch's reviewed import and "Send
  to Zotero" dialogs are not carried over; a one-shot send can come back
  later as "link, force push, detach" if anyone misses it.
- **Lift the branch's pure parts, not its flows.** Carried as modules:
  `zotero_web.py` (client, throttle, paged reads, files),
  `zotero_fields.py` with `zotero_schema.json` (item ↔ record),
  `zotero_account.py`, `zotero_oauth.py`, `routers/zotero.py` and
  `SettingsZotero.jsx` (the account's key, OAuth through the server's own
  app or Gamma Cloud's broker, a pasted key), the Gamma Cloud broker in
  `cloud/`, `tests/zotero_fake.py` and the tests of those modules. Carried
  as functions into the new modules: `pull_fields`, `pull_set`,
  `first_link_snapshot`, `note_hash`, `lossy_note`, `note_marks`,
  `annotation_records` and its position readers, `object_key`. Left:
  `_ZoteroWeb` in `routers/imports.py`, `zotero_library.py`'s listing and
  plan, `zotero_push.py`, `ZoteroPushDialog`, the `zotero-libraries` pref
  and `zotero_item` on pages.
- **The engine's state lives in one table, pages carry identity only.**
  `zotero_objects` (pages.db) maps every synced Zotero key to its block or
  page with the version and base it was last synced at; blocks keep the
  identity properties the RDF path already reads (`zotero_key` on a page,
  `zotero_note`, `zotero_annotation`), so exports keep working.
- **Annotations are items; PDFs go up clean.** No copy with burned-in
  highlights is uploaded; a PDF goes up once, when Zotero lacks it, and
  again only when the page's file is replaced.
- **Resolution without asking, rows for the record.** The mirror's rule:
  an edit beats a delete, a change on one side wins, a change on both
  keeps Gamma's here and Zotero's there and writes a `sync_conflicts` row
  the person can settle (keep, mine, theirs).

### Modules

Backend (`backend/gamma/`):

- `zotero_web.py` (lifted): add `versions(library, kind, since)`,
  `deleted(library, since)`, `objects(library, kind, keys)` (by key, 50 a
  request, `includeTrashed=1`), `write(library, kind, objects, version)`
  returning `(successful, unchanged, failed)`, `trash(library, keys,
  version)`, `stream` later. `KEY_CONCURRENCY`, the throttle and the
  redirect handling stay.
- `zotero_fields.py`, `zotero_schema.json` (lifted as they are).
- `zotero_notes.py` (dev, as it is).
- `zotero_annotations.py` (new): the table above in code. `to_item(block,
  page_frame, glyphs)` → one or more annotation JSONs (an ink group one
  per bucket; a position past 60,000 bytes split as the client splits);
  `from_item(data, page_frame)` → the block's `pdf_position`/`text_box`,
  colour, quote and comment (from `annotation_records`); `sort_index(rect,
  page_chars, height)` as `selection.js` computes it; `hash(projection)`
  over `(type, colour, position as sent, text, comment)` for the three-way
  rule; the key rules.
- `zotero_sync.py` (new): the registry (`zotero_links`), `sync_workspace`
  (one round, locked per workspace), `_pull`, `_push`, `_record`, force
  and detach, the conflict and log writers shared with the mirror
  (`sync_engine._conflict`, `_note` and the `sync_conflicts`/`sync_log`
  tables are reused as they are).
- `sync_clock.py` (extracted from `sync_engine`): the loop, `due_now`, the
  on-commit debounce and `request_sync`, taking any engine that offers
  `rows()`, `run(ws)` and `wants_change(ws)`; both engines register. About
  a hundred lines move; `sync_engine`'s public names stay.
- `routers/zotero_sync.py` (new): `/api/zotero/links`, the mirror API's
  verbs one for one (below).
- `db.py`: `zotero_links` in users.db, `zotero_objects` in pages.db, both
  `CREATE IF NOT EXISTS`; `routers/prefs.py` keeps refusing
  `zotero-account`.

Frontend (`frontend/src/`):

- `settings/SettingsZotero.jsx` (lifted): the account row under Account &
  sync.
- `settings/SettingsMirrors.jsx`: the Clones section lists mirrors and
  Zotero links together, each row with its kind's icon, and a second
  button "Link a Zotero library…" whose dialog offers the libraries the
  key reaches (My Library, the groups), Into (a new workspace or one of
  mine), Direction (two-way, receive only), Files (download PDFs, upload
  PDFs Zotero lacks) and a Connect Zotero… step when no key is connected.
- `collaboration/MirrorPopover.jsx`: `mirrorState` and the popover take
  the link's info with `kind: "zotero"`; the words change ("origin" →
  "Zotero", the log's "pulled from remote" → "pulled from Zotero"), the
  actions do not. `conflictKinds.js` gains the kinds below.
- `transfers/zoteroApi.js` (lifted, trimmed): the account calls plus the
  links' calls. `App.jsx` loads `/api/zotero/links` beside `/api/mirrors`
  for the pill and the merge chips.
- `shared/i18n/locales/zh.json`: the new strings.

### Data

`zotero_links` (users.db), one row per workspace:

| column | meaning |
|---|---|
| `workspace_id` PK, `owner` | as `mirrors` |
| `library`, `library_name` | `users/<id>` or `groups/<id>`, and its name |
| `mode` | `two-way`, `pull`, `off` (detached) |
| `poll_s`, `on_change` | the cadence, as `mirrors` |
| `files` | `download`, `both`, `none` |
| `cursor` | the library version every object at or below which is applied here |
| `local_cursor` | the change log seq every page at or below which is pushed |
| `status`, `created_at` | as `mirrors`: `running`, `progress`, `last_sync`, `last_attempt`, `last_error`, the round's counts (`pages_pulled`, `pages_pushed`, `annotations_pulled`, `annotations_pushed`, `trashed`, `conflicts`), `pending_local` |

The key is the owner's account connection (`zotero-account`); a link whose
owner has no connection pauses with `last_error` "Connect your Zotero
account", as a mirror with a refused token does.

`zotero_objects` (pages.db), one row per synced Zotero object:

| column | meaning |
|---|---|
| `key` PK | the Zotero key |
| `kind` | `item`, `attachment`, `note`, `annotation`, `collection` |
| `page_id`, `block_id` | where it lives here (a collection's `block_id` is the folder id; an attachment's is the page) |
| `version` | the object's Zotero version when last synced |
| `base` | the three-way base: for an item the projected record, tags and collection paths (`first_link_snapshot`'s shape); for a note or annotation the hash; for an attachment the md5 |
| `synced_at` | |

A block deleted in Gamma is a row whose block is gone; an object deleted in
Zotero is a key `/deleted` or `deleted: 1` names. Ink groups have one row
per bucket item, each naming the same block.

### The round

0. **Access.** The owner's key through `zotero_account`; `keys/current`
   once per 15 minutes (as `_round_whoami`); a library the key cannot write
   drops the round to pull, as `_check_remote` drops a mirror.
1. **Is there anything?** `items?limit=1&format=versions` with
   `If-Modified-Since-Version: cursor`; 304 skips the pull.
2. **Pull**, in this order so parents exist before children:
   collections changed since the cursor (`collections?since=`), items
   changed (`items?since=&includeTrashed=1&format=versions`, then by key),
   `/deleted?since=`. Each object by kind:
   - a collection → a folder (the folders pseudo-page, through ops as the
     mirror files them); `deleted: true` or in `/deleted` → the folder goes
     unless a page filed there changed here (row `zotero_deleted_edited`);
   - a top-level item → the page (`zotero_fields.meta_from_item`; a
     standalone note → a note page; a standalone PDF → a page without a
     record): new → created in the folders of its collections, labelled
     with its tags; known → `pull_fields`/`pull_set` three ways against
     the row's base (a field both changed: Gamma's kept, row
     `zotero_field` with both values); `deleted` → the page to Gamma's
     trash unless changed here since the base (row);
   - an attachment → the page's PDF when the page has none and `files`
     allows: `imported_file` and `imported_url` PDFs only, snapshots and
     zips skipped with a warning; downloaded through the redirect without
     Zotero's headers, MD5 checked, stored through `storage.put_upload`;
   - a note → `zotero_notes.note_tree`, the branch's three-way rule with
     `lossy` (row `zotero_note`, textual, when both changed);
   - an annotation → `zotero_annotations.from_item`: new → inserted under
     the page (as the embedded importer inserts); known with a newer
     version → the block's colour, place, quote and comment set through
     ops while the block's own hash is still the base's, else row
     `zotero_annotation` with both projections; `deleted` → the block
     deleted unless changed here (row); one Zotero would not let this user
     edit (another member's, in a group) is marked read-only in its row so
     the push never writes it.
   When a later answer's `Last-Modified-Version` is above the first's, the
   pull runs once more from the cursor, at most three times (the client's
   restart), then leaves the rest to the next round. The cursor advances
   to the first answer's version only after everything the pass fetched
   is applied.
3. **Push.** The local change feed from `local_cursor` (read in-process
   as the mirror reads it, the engine's own writes tagged client `zotero`
   and skipped) names the pages touched; per page, the rows against the
   blocks:
   - a page with no `item` row → create (a record → its type's item from
     `zotero_fields.fields_from_meta`; none → a `document` titled as the
     page; a note page → a standalone note), filed in the collections of
     its folders (created on the way), tagged with its labels; with a PDF
     and `files` `both` → the attachment and the three steps (`exists`
     dedupes by md5);
   - a known page whose record, labels or folders differ from the base →
     the branch's field, tag and collection merge → PATCH;
   - blocks whose hash differs from their row's base → `to_item` → create
     (derived key, `version: 0`) or PATCH with the row's version; a row
     whose block is gone → trash;
   - a page in Gamma's trash → the item trashed; one deleted for good → the
     item trashed too (never `DELETE`).
   All in batches of 50 with `If-Unmodified-Since-Version: cursor`; a
   `failed` 412 marks its object to be pulled first next round (Zotero's
   change lands here, then Gamma's goes if it still differs under the
   rule); any other failure is a `zotero_rejected` row with Zotero's
   sentence (a position too long after splitting, a label too long, a
   quota).
4. **Record.** Rows, versions and bases in `zotero_objects`, the cursors,
   the status, a `sync_log` line per page (`pulled`, `pushed`, `created
   here`, `created there`, `trashed here`, `trashed there`, with the
   annotation counts), the identity properties on new blocks through the
   op path.

Force pull replays everything from version 0 taking Zotero's side (rows
for what it replaced); force push takes Gamma's side for every known
object and creates the rest; detach sets `mode` off; relink keeps the
rows; delete removes the link and the rows, the workspace stays.

### Conflict kinds

`zotero_field` (a record field both changed; mine/theirs the values),
`zotero_note` (textual), `zotero_annotation` (colour, place or comment both
changed; theirs the projection, resolved "theirs" applies it),
`zotero_deleted_edited` (an edit beat a delete, either way),
`zotero_locked` (another member's annotation changed here: not sent),
`zotero_lossy` (a note not written back because Zotero's holds structure
Gamma cannot rebuild), `zotero_rejected` (Zotero refused a write). Each is
a `conflictKinds.js` entry with its sentence and glyph.

### API

`/api/zotero/links`, the mirror API's shapes: `GET` the owner's links
(`kind: "zotero"`, the row, `status`, `conflicts_open`,
`conflicts_newest`, `pending_local`); `POST {library, workspace_id?,
name?, mode, files}` (checks the connection and the library's access
first, creates or links the workspace, requests the first round); `GET
/{ws}`; `PATCH /{ws} {poll_s, on_change, mode, files}`; `POST /{ws}/sync?wait=`;
`POST /{ws}/detach`; `POST /{ws}/relink`; `POST /{ws}/force {direction}`;
`DELETE /{ws}`; `GET /{ws}/log`; `GET /{ws}/conflicts`; `POST
/{ws}/conflicts/{id} {choice}`. Owner only, personal accounts only, as
`routers/mirrors.py`.

### Phases

Each is one commit-sized step on a `zotero-sync` branch from `dev`, in its
own worktree; the branch merges to `dev` when phase 5 is green.

1. **Foundation.** Lift the modules named above from `zotero-cloud`
   (`git checkout zotero-cloud -- <paths>`), drop the import-only parts,
   add the four client methods; the account row in Settings; tests
   `test_zotero_web.py`, `test_zotero_fields.py`, `test_zotero_account.py`,
   `test_zotero_oauth.py` and `zotero_fake.py` lifted, the fake taught
   annotations under attachments, `itemType=annotation`, `/deleted` for
   items, `deleted: true` and the restart on a moved version.
2. **`zotero_annotations.py`** with `test_zotero_annotations.py`: the six
   kinds both ways on the fixtures saved from this library (the reader's
   own shapes, anonymised), the sort index against the values the reader
   wrote (`00000|000377|00132` for a known rect), the ink buckets and the
   split, the hash's stability.
3. **The engine's pull**, `zotero_links`, `zotero_objects`, the router,
   `sync_clock.py`, the first round as the import, the Settings dialog
   and the pill with `kind`; `test_zotero_sync.py` for the first fill,
   an incremental pull, trash and delete, a moved version mid-walk,
   detach and relink.
4. **The push**: creates, updates, trash, files, the 412 round trip,
   force both ways; tests for each, and for the rule table (one side,
   both sides, edit against delete, locked, lossy, rejected).
5. **Surfaces and docs.** The conflict kinds and log words in the
   popover and the card, zh strings, the e2e scenario `zotero-link.mjs`
   on the fake served over HTTP (the branch's `zotero.mjs` shows the
   harness), `docs/dev/zotero_sync.md` (new, the mechanics), a paragraph
   in `mirror.md`, `settings.md`, `import_export.md` and the user guide,
   this note's "Outcome".
6. **Live run.** An isolated instance (port 9100 and up, its own data
   directory, the verify skill) linked to this library with the key:
   the first fill, an edit in Gamma seen through the API, an edit through
   the API seen in Gamma, a trash each way, a forced pull; then a reader:
   a desktop signed in to the account, or the web library's reader on
   zotero.org, opening the test PDF.

Then the `zotero-cloud` branch and its worktree go (its unmerged work is
superseded; asked first).

### Still open

- Group libraries: none on this account; the lock rule rests on the
  client's code.
- `nextPageRects`, EPUB and snapshot annotations: read as highlights on
  the first page or skipped; not in the plan.
- The streaming socket as the trigger: after the poll works.
- The local API for WebDAV and linked-file users: after the Web API path.

## Outcome (October 2026)

The plan is built on the `zotero-sync` branch.
[dev/zotero_sync.md](../dev/zotero_sync.md) describes the engine, and the
[user guide](../user_guide/user_guide.md#zotero-link) how a person uses it.
These decisions were taken as planned:

- one engine both ways on the clone's surfaces and clock (Settings →
  Workspaces → Clones, the pill, the log, the conflict cards, cadence,
  direction, force, detach and reattach), with no reviewed one-shot import
  or send;
- the branch's pure modules lifted (`zotero_web`, `zotero_fields` with its
  schema, `zotero_account`, `zotero_oauth`, `routers/zotero.py`,
  `SettingsZotero.jsx`, Gamma Cloud's broker, the fake Zotero), not its
  flows;
- annotations as items, and a PDF uploaded clean;
- differences settled without asking, Gamma's side kept and Zotero's in a
  conflict row;
- trash both ways, never a `DELETE`.

Where the build differs from the plan, and why:

- **Two bases, not one.** Each synced object's row keeps Zotero's
  projection as last seen (`zbase`) and Gamma's as last synced (`gbase`).
  A side changed a field when its value left that side's base
  ([Merging](../dev/zotero_sync.md#merging)). With one base or a hash,
  every lossy mapping (a quote clipped, a `<sub>` dropped, a position
  rounded) read as an edit of the other side, which the next round sent
  back. Of the functions the plan would carry from the branch only
  `object_key` survives: `pull_fields`, `pull_set`, `first_link_snapshot`,
  `note_hash`, `lossy_note`, `note_marks` and `annotation_records` gave way
  to `zotero_merge`'s rules and `zotero_annotations`.
- **Three tables in pages.db.** `zotero_objects` sits beside a
  `zotero_queue` of what is listed and not yet applied, and a
  `zotero_state` with the library version, the local cursor and a first
  link's progress ([Data](../dev/zotero_sync.md#data)). The cursors left
  users.db's `zotero_links` so a workspace backup restores them with its
  pages; the row keeps the settings and the status, one per owner and
  library. The link's `files` became `download` (every PDF, or each as its
  page opens), `upload`, `auto_tags` and `send_unrecorded`. Pages carry
  two marks besides identity: `zotero_pdf` (a PDF Zotero holds and Gamma
  not yet) and, on a page or a block Gamma will not write back,
  `zotero_readonly`.
- **A queue of units, not a pull then a push.** Both sides' changes go
  into one queue. A unit is a top-level item with its children: its
  record, tags, collections, trash, PDF, notes and annotations are decided
  and written on both sides before the next unit starts. A round stops
  after 60 s or 50 units and the next follows at once, so a first fill of
  thousands of items is many short rounds that a restart resumes
  ([The round](../dev/zotero_sync.md#the-round)). The listing, not the
  application, moves the library version, so the client's restart on a
  version that moved mid-walk was not needed. A page whose newest change
  is the link's own write is not visited again. A fill reads an item's
  children by key along with it: a generated 2,000-item library's first
  fill went from 2,450 requests to 411.
- **Conflicts.** An eighth kind, `zotero_file`, covers a PDF replaced on
  both sides. Gamma's goes to Zotero, and Zotero's file is kept here, and
  spared by the uploads clean-up, while the conflict is open. The choices
  are keep and Take Zotero's, plus Send anyway for a lossy note.
  Take Zotero's is refused (409) once Zotero changed the thing again
  since the conflict, since it would write an older value over a newer
  one. On a receive-only link Gamma's side stays in Gamma alone, and the
  row and its card say so
  ([Conflicts and the log](../dev/zotero_sync.md#conflicts-and-the-log)).
- **First link and force**
  ([First link and force](../dev/zotero_sync.md#first-link-and-force)):
  - Pairs are written before anything is reconciled, so an interrupted
    first link resumes without duplicates.
  - A value one side lacks goes to the other, whichever side wins.
  - Where the two PDFs differ, Zotero's file is taken whenever its
    attachment holds annotations, and Gamma's highlights move onto it, so
    both sides' annotations end on one file.
  - "A force deletes nothing" became "a force takes its side's trash". A
    force pull sends to Recently deleted, or removes, what Zotero holds in
    its trash, and a force push does the same the other way, so the two
    sides match after it.
  - Notes access granted later lists the library again and pairs the
    notes already here as a first link does.
- **PDFs.** A download can wait until its page opens ("When opened", the
  50 newest items' at once). A page waiting for its PDF shows a card, and
  refuses another attach while a link or a clone brings the file. A
  replaced file waits while its highlights are placed, then goes over
  Zotero's, with uploads on, only when every annotation under it is one
  Gamma may move, else as a new attachment. After Zotero's 413, no PDF
  goes for 24 hours or until Sync now ([PDFs](../dev/zotero_sync.md#pdfs)).
- **Notes** are diffed block by block, not hashed and replaced
  ([Notes](../dev/zotero_sync.md#notes)). A Zotero note keeps the blocks it
  came from, and the page's other notes go as one "Notes from Gamma" note
  after a minute of quiet. A note Gamma cannot rebuild is read-only, and
  Zotero's note pictures go back as they came.
- **Annotations** ([Annotations](../dev/zotero_sync.md#annotations)):
  - A comment is the block's own note and the notes under it (the RDF
    export's popup text), not the first child note alone.
  - A block that cannot travel now (its PDF not here yet, the PDFs
    differ, dashed after a replacement, still drawn in the PDF) is held,
    never trashed.
  - Each ink item owns its strokes, so a change of look patches that item.
  - Another member's annotation in a group is read-only, and a Gamma
    delete of it is put back.
  - The sort index follows the reader's rule as corrected above.
- **The stream** was not left for later: it triggers the Live cadence,
  with a safety round every 30 s. The clock runs up to four links' rounds
  at once ([Clock and stream](../dev/zotero_sync.md#clock-and-stream)).
- **Signing in** goes through the server's own Zotero app or Gamma Cloud's
  broker, else a pasted key
  ([Accounts and access](../dev/zotero_sync.md#accounts-and-access)).

Still open:

- Phase 6, a run against zotero.org and a look at the result in Zotero's
  reader. The engine is tested against the fake Zotero, built on this
  library's shapes.
- A group library, which this account lacks, so the lock rule still rests
  on the client's code.
- The local API for WebDAV and linked-file users.
- Highlights across pages (`nextPageRects`, the first page only), and
  EPUB and snapshot annotations, which are set aside.
- A Zotero note's paragraphs come back as list items after a Gamma edit,
  and nobody has checked whether Zotero's editor keeps a picture inside a
  list item.
- Whether the reader draws a `text` annotation upright when its rotation
  equals the page's `/Rotate`.

## Sources

- [Zotero Web API v3: basics](https://www.zotero.org/support/dev/web_api/v3/basics)
- [Zotero Web API v3: syncing](https://www.zotero.org/support/dev/web_api/v3/syncing)
- [Zotero Web API v3: write requests](https://www.zotero.org/support/dev/web_api/v3/write_requests)
- [Zotero Web API v3: file uploads](https://www.zotero.org/support/dev/web_api/v3/file_upload)
- [Zotero Web API v3: OAuth](https://www.zotero.org/support/dev/web_api/v3/oauth)
- [Zotero Web API v3: full-text content](https://www.zotero.org/support/dev/web_api/v3/fulltext_content)
- [Zotero Web API v3: streaming API](https://www.zotero.org/support/dev/web_api/v3/streaming_api)
- [Zotero local API](https://www.zotero.org/support/dev/web_api/v3/local_api)
- [Zotero 10 for developers](https://www.zotero.org/support/dev/zotero_10_for_developers)
- [Zotero changelog](https://www.zotero.org/support/changelog) (10.0 on 17 August 2026)
- [Zotero storage plans](https://www.zotero.org/storage)
- [dataserver `model/Item.inc.php`](https://github.com/zotero/dataserver/blob/master/model/Item.inc.php) and [`model/Items.inc.php`](https://github.com/zotero/dataserver/blob/master/model/Items.inc.php): annotation validation, `$maxAnnotationTextLength = 7500`, `$maxAnnotationPageLabelLength = 50`, `$maxAnnotationPositionLength = 65535`
- [client `xpcom/annotations.js`](https://github.com/zotero/zotero/blob/main/chrome/content/zotero/xpcom/annotations.js) (types, colours, `ANNOTATION_POSITION_MAX_SIZE`, `toJSONSync`, `saveFromJSON`, `splitAnnotationJSON`), [`xpcom/data/item.js`](https://github.com/zotero/zotero/blob/main/chrome/content/zotero/xpcom/data/item.js) (parent and sort-index rules, group editability), [`xpcom/sync/syncAPIClient.js`](https://github.com/zotero/zotero/blob/main/chrome/content/zotero/xpcom/sync/syncAPIClient.js), [`syncEngine.js`](https://github.com/zotero/zotero/blob/main/chrome/content/zotero/xpcom/sync/syncEngine.js), [`syncLocal.js`](https://github.com/zotero/zotero/blob/main/chrome/content/zotero/xpcom/sync/syncLocal.js)
- [reader `src/common/types.ts`](https://github.com/zotero/reader/blob/master/src/common/types.ts) (`PDFPosition`) and [`src/pdf/selection.js`](https://github.com/zotero/reader/blob/master/src/pdf/selection.js) (`getSortIndex`)
- Page labels: [zotero/pdf.js](https://github.com/zotero/pdf.js) `src/core/module/page-label.js` (`10.0`); the document worker (`document-worker`) `src/pdf/structure/page-label.js` (master); [mozilla/pdf.js](https://github.com/mozilla/pdf.js) `src/core/catalog.js`, `name_number_tree.js`, `string_utils.js`, `core_utils.js`; [zotero/reader](https://github.com/zotero/reader) `src/pdf/pdf-view.js` (`_getPageLabel`), `src/pdf/pdf-document-data.mjs`
- [Forum: expose an API for programmatic PDF annotation creation (June 2026)](https://forums.zotero.org/discussion/132273/feature-request-expose-api-for-programmatic-pdf-annotation-creation-highlights-notes)
- [Forum: why are annotations by other users locked (2022 to 2026)](https://forums.zotero.org/discussion/96174/zotero-pdf-reader-why-are-annotations-by-other-users-locked)
- [Forum: write support in local API (July 2026)](https://forums.zotero.org/discussion/comment/517142)
- [Forum: area annotations are not synced (image caches)](https://forums.zotero.org/discussion/119530/area-pdf-annotations-are-not-synced-with-the-server)
- [Forum: web PDF reader annotations support (2024)](https://forums.zotero.org/discussion/99390/web-pdf-reader-annotations-support)
- [zotero-dev: ItemAnnotation fields (sort index, rects)](https://groups.google.com/g/zotero-dev/c/1BZJIJ-Gd0o)
- [zotero-mcp PR 609: sort PDF annotations in reading order](https://github.com/54yyyu/zotero-mcp/pull/609) (highlights created through the local API of Zotero 10.0.3, listed in reading order)
- [zotero-mcp PR 472: Zotero 10 local write endpoints](https://github.com/54yyyu/zotero-mcp/pull/472)
- [pyzotero issue 270: local API returns no annotations](https://github.com/urschrei/pyzotero/issues/270)
