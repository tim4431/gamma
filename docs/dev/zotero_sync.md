# Zotero link

A **Zotero link** keeps a personal Gamma workspace in step with one Zotero
library on zotero.org, My Library or a group library, through Zotero's Web
API. A paper added, highlighted or filed in Zotero arrives in the workspace,
and what the person does in Gamma (a highlight, a note, a record field, a
label, a folder, a replaced PDF) reaches Zotero, where the desktop client and
the library's other clients pick it up. Gamma is one more client of the
library beside Zotero's desktop: the same API, the person's API key,
Zotero's versions and refusals. The survey behind it (what the API offers,
what an annotation item is, what a run against a real library confirmed) is
the research note [zotero-sync.md](../research/zotero-sync.md); this guide
describes the engine as built.

## Overview

A link is one row of users.db `zotero_links` naming a workspace, its owner
and a library (`users/<id>` or `groups/<id>`). Everything the link knows of
each Zotero object lives in the workspace's pages.db (`zotero_objects`,
`zotero_queue`, `zotero_state`), so a workspace backup restores it with the
pages it describes. The workspace stays an ordinary personal workspace: the
person edits pages and blocks as anywhere else, the engine reads those edits
from the local change feed, and it writes Zotero's changes through the op
path.

The link has three modes (`zotero_links.mode`):

- **two-way**: Zotero's changes come in and Gamma's go out;
- **receive only** (`pull`): Zotero's changes come in, Gamma's stay here
  until the link is two-way again. A two-way link whose key cannot write
  runs its rounds receive only (`status.mode` is the round's own mode);
- **detached** (`off`): no round runs and the workspace lists as an
  ordinary one, but the row and every pages.db row are kept, so a reattach
  merges what both sides did meanwhile.

The link is built on the clone's model and surfaces ([mirror.md](mirror.md)):
the header's sync pill and popover, the row in Settings → Workspaces →
Clones and Zotero links, the log, the conflict chips and cards, cadence, direction, Sync
now, force pull and push, detach, reattach and remove. The two engines
share the clock ([Clock and stream](#clock-and-stream)), users.db's status
JSON conventions, and pages.db's `sync_log` and `sync_conflicts` tables
(written through `sync_engine._note` and `sync_engine._conflict`). They
share no sync logic. The mirror moves Gamma's own ops between two Gamma
servers and merges each block three ways against the tree both last held.
Zotero has no op log and merges nothing: every object carries a version, a
write names the version it was decided on, and a stale write is refused
whole (412). So the Zotero engine keeps, for every object it syncs, two
comparable projections in Zotero's terms (Zotero's object as last seen,
Gamma's as last synced) and decides field by field which side changed what
([Merging](#merging)).

A workspace is a clone, a publication's mirror or a Zotero link, never two
of these: `sync_engine.create_mirror` and `publish.publish` refuse a
workspace with a `zotero_links` row (detached included), and
`zotero_sync.create_link` refuses one with a `mirrors` row, but for the row
of a publication whose last page was unpublished, which it forgets once
the link is made ([Refusals](#refusals)).

## Modules

All in `backend/gamma/` unless named otherwise.

| Module | What it owns |
|---|---|
| `zotero_sync.py` | The public face: the registry (`create_link`, `set_settings`, `detach_link`, `relink_link`, `force_sync`, `remove_link`, `fetch_page`, `lift_quota`, `preview`), the round and its steps (`sync_workspace`, `_round`, `_access`, `_own_library`, `_start_force`, `_list_zotero`, `_list_gamma`, `_work`, `run_unit`, `_record`), `resolve_conflict` and its `RESOLVERS`, what the stream calls (`forget_access`, `record_error`), what the API shows (`pending_local`, `following`, `remote_url`), `reset_interrupted` and the clock's registration |
| `zotero_sync_rows.py` | What every part shares: the op-log actor and client (`ACTOR`, `CLIENT`) and the clock's settings (`ENGINE`, `LIVE_POLL_S`, `LIVE_SAFETY_S`, `DEBOUNCE_S`, `WORKERS`), the link's stored row (`get_link`, `list_links`, `LINKS`, `save_link`, `patch_status`), the round's state (`Round`, its `COUNTERS`), the `zotero_objects` rows (`read_rows`, `write_rows`, `new_row`, `drop_rows`, `signature`), `CONFLICT_KINDS`, `ONE_OPEN`, `record_conflict`, `field_base`, `page_note`, the words `HELD`, `SETTLED`, `STOPS_ROUND`, `KEPT_HERE`, `MOVED_ON` |
| `zotero_sync_reads.py` | What units read: a page's Gamma side (`Side`, `page_side`, its PDF's MD5 read only when needed), the projections both sides are compared in (`gamma_item` / `zotero_item`, a standalone PDF's and a note page's), shared row rules (`HELD_ROWS`, `moving_pdf`), Zotero objects read by key ahead of their units (`read_ahead`, `read_unit`, `_listed_children`) |
| `zotero_sync_items.py` | An item's unit (`_Unit`, `reconcile`, `unit_item`, `unit_child`): record, tags, collections, trash, a note page's note, the writes and their outcomes |
| `zotero_sync_pdfs.py` | The PDF part of an item's unit (`UnitPdf`, `_file_plan`, `pair_side`), a new attachment and the PDF it went beside (`_beside`, `_beside_again`, `_back_beside`), a file's own unit (`unit_file`: downloads, uploads), kept copies of Zotero's file (`_hold_copy`, `_went`), the wait while a page's highlights are placed (`relocation_ended`), Zotero's full storage (`quota_step`); `STORAGE_WAIT`, the reason of a download held back by this server's full storage |
| `zotero_sync_blocks.py` | An item's notes and annotations (`Blocks`), whether a page's blocks differ from what last synced (`blocks_dirty`), the pages to check once for their page labels (`label_checks`) or for a text box to make a Zotero box (`box_checks`), what Zotero removed for good of them (`children_gone`), the note, lossy-note and annotation resolvers |
| `zotero_sync_pages.py` | A Gamma page's unit (`unit_page`): a page made an item (batched creates), a standalone PDF given a parent item (`_send_move`), a create derived again (`_resend`), a note page or a standalone PDF's page going back to Zotero (through `send_back`) |
| `zotero_sync_gone.py` | Items Zotero removed for good (`items_gone`, `_goes_back`), a page changed here sent back as a new standalone note or PDF (`send_back`), a key Zotero did not answer for (`absent`) |
| `zotero_sync_first.py` | A first link's pairs (`first_match`), note pages paired when notes access arrives (`first_notes`), the link dialog's preview of a first link (`preview_first_link`) |
| `zotero_sync_resolve.py` | The `zotero_field` and `zotero_file` resolvers |
| `zotero_sync_folders.py` | Collections ↔ folders (`collections_pass`, `_Folders`, `collections_gone`) |
| `zotero_merge.py` | The pure decisions: projections, `merge` and `bases_after`, `first_link_base`, note regions (`represented`, `note_edits`, `note_removal`), `note_lossy`, the primary attachment (`primary`, `fetchable`), first-link matching (`match`, `match_paths`), which pages become items (`wants_item`, `standalone_gets_parent`), the readable lines of a conflict |
| `zotero_local.py` | Gamma-side reads and writes: `read_page` (`PageView`: regions, annotation blocks), the transaction (`apply`, `create_page`, the writers that commit on their own), `set_pdf`, op builders (records, filing, folders, note edits, annotation blocks, marks), note pictures |
| `zotero_remote.py` | Zotero-side I/O for a round: `list_changes`, the queue and state helpers, reads by key (`fetch_items`, `descendants`, `fetch_unit`), the batched `Writes`, file transfers (`download_pdf`, `upload_pdf`, `download_image`), `error_kind` |
| `zotero_annotations.py` | Annotation items ↔ annotation blocks: `to_items`, `from_item`, `apply_fields`, `projection`, the sort index, colours, comments, ink strokes, position splits, `hold_reason` and `eligible` |
| `zotero_notes.py` | Note HTML ↔ block trees (`note_tree`, `note_html`), shared with the RDF import and export |
| `zotero_import.py`, `zotero_export.py` | The RDF import and export ([import_export.md](import_export.md#zotero-library-import)); the link borrows the import's note-page rule (`_note_page`, through `zotero_sync_blocks.note_page_parts`) and its note text (`html_note_text`) |
| `zotero_fields.py` (+ `zotero_schema.json`) | Item data ↔ a page's record (`meta_from_item`, `fields_from_meta`, `normalized`, `patch_for`, `retype`), per Zotero's schema |
| `zotero_web.py` | The Web API transport and `ZoteroClient`: the key's throttle, retries, paging, versions, reads by key, writes, file download and upload; `object_key`, `random_key`, `item_uri` |
| `zotero_stream.py` | Zotero's streaming API as the trigger of Live links |
| `zotero_account.py` | The account's Zotero connection: the sealed API key in the `zotero-account` preference |
| `zotero_oauth.py` | OAuth 1.0a with zotero.org, for a server with a Zotero app of its own |
| `sync_clock.py` | The clock both engines register with (`Engine`, `LinkTable`) |
| `routers/zotero.py` | `/api/zotero/account`, `/api/zotero/libraries`, the OAuth round trip |
| `routers/zotero_sync.py` | `/api/zotero/links`: the link API |
| `cloud/gammacloud/zotero.py` | Gamma Cloud's Zotero broker ([cloud_accounts.md](cloud_accounts.md#connecting-zotero)) |

Frontend, in `frontend/src/`: `collaboration/syncLinks.js` (pure: which
API a link lives under and what a Zotero link's status means),
`collaboration/MirrorPopover.jsx`, `MergeResolver.jsx` and
`conflictKinds.js` (the clone's pill, cards and conflict words, given a
Zotero `kind`), `collaboration/ZoteroPage.jsx` (the PDF card and the
read-only badge), `settings/SettingsZotero.jsx` (the account row),
`settings/ZoteroLinkDialog.jsx` with `settings/zoteroPreview.js` (the link
dialog), `settings/SettingsMirrors.jsx` (the links' rows),
`transfers/zoteroApi.js`. The desktop shell's panel reads the links in
`desktop/lib/keeping.js` and `desktop/main.js`.

The parts a round runs import one way: rows, then blocks, then reads,
then pdfs, then gone, then items, then pages; `zotero_sync_pdfs.py`
imports only reads and rows, `zotero_sync_folders.py` only rows,
`zotero_sync_first.py` and `zotero_sync_resolve.py` only reads and rows,
and none of them imports `zotero_sync.py`. `run_unit` looks each unit up
on its module as it runs, so a test that replaces one reaches it.

## Data

### users.db: the link and the account's key

`zotero_links` (migration step 45, [migrations.md](migrations.md); the
step also drops duplicate owner-library rows, keeping the oldest, before it
makes the unique index):

| Column | Meaning |
|---|---|
| `workspace_id` | The workspace kept in step (primary key) |
| `owner` | The account whose Zotero key the link uses; the workspace must stay a personal one this account owns |
| `library`, `library_name` | `users/<id>` or `groups/<id>`, and its name as Zotero gives it (a group renamed in Zotero is renamed here by the next round that reads the key's access, at most 15 minutes on) |
| `mode` | `two-way`, `pull` (receive only) or `off` (detached) |
| `download` | `all`: every PDF in Zotero Storage comes; `open`: a PDF comes when its page is opened, and those of the 50 newest items at once (`NEWEST_FILES`) |
| `upload` | Gamma's PDFs go to Zotero where its item has none, and replacements go too |
| `auto_tags` | Zotero's automatic tags (type 1) become labels too |
| `send_unrecorded` | A PDF page without a paper record goes to Zotero as a `document` |
| `poll_s`, `on_change` | The cadence: 5 is Live (the streaming API, with a safety round every 30 s), 0 only by hand, else seconds (at most `sync_clock.POLL_MAX_S`, a day); `on_change` asks for a round 3 s after a local write. A new link is Live with `on_change` on (the columns' defaults: `create_link` sets neither) |
| `status` | JSON, only ever patched (`LINKS.patch_status`) |
| `created_at` | |

`idx_zotero_links_owner_library` is unique on `(owner, library)`: an
account links a library once. `create_link` checks it first; the index is
the backstop, and its `IntegrityError` is answered with the same refusal.
The row goes with its workspace (`workspaces._delete_rows`) and with its
owner's account.

The status JSON holds what the mirror's does where the keys mean the same
(`running`, `started_at`, `progress`, `interrupted`, `last_sync`,
`last_attempt`, `last_error`, `detached_at`, `detached_mode`, `force`,
`adopt`) and:

- `mode`: the round's own direction (`pull` while the key cannot write);
  `access` `{write, files, notes}` and `username`, as the last round found
  the key;
- `error_kind`: what `last_error` is, read by the frontend instead of the
  sentence: `offline`, `auth`, `not_connected`, `library_gone`, `busy`,
  `other` (`zotero_remote.error_kind`);
- the round's counters (`pages_pulled`, `pages_pushed`, `pages_deleted`,
  `files_pulled`, `files_pushed`, `blocks_added`, `blocks_removed`,
  `blocks_changed`, `annotations_pulled`, `annotations_pushed`,
  `notes_pulled`, `notes_pushed`) and `backlog`, the queue's size;
- `waiting`: what waits on something outside Gamma, shown and never an
  error: `files` `{reason: n}` (`linked`, `unavailable`, `no_access`,
  `quota`, `storage`, `failed`, and `placing`, a PDF replaced here waiting
  while its highlights are placed), `annotations` `{n, reasons: {sentence:
  n}}`, `notes` (a count) and `quota_until` (Zotero's storage is full until
  then);
- `progress` while a round runs: `{phase: "first" | "sync", done, total,
  page, at, bytes: {done, total}, file: {name, done, total, dir}}`, `done`
  and `total` counted over the whole fill; `fill` `{units, bytes}` carries
  those counts from one bounded round to the next that follows it at once;
- `forced` (the direction of a force under way), `restore_seen` (the last
  workspace restore a round handled).

The API adds `pending_local`, `conflicts_open`, `conflicts_newest`,
`detached` and `interval_s` when it shows a link ([Routes](#routes)).

The account's connection is the account-wide preference `zotero-account`
(`zotero_account.PREF_KEY`): `{user_id, username, key, hint, access, via,
connected_at}`, `key` sealed with the data directory's Fernet key
(`publisher_sessions.seal`, as the mirrors' tokens are), `via` `oauth` or
`key`. The preference is reserved: `/api/prefs` refuses it and the synced
profile never carries it ([Accounts and access](#accounts-and-access)).

### pages.db: objects, queue, state

The three tables are in `PAGES_SCHEMA` (no migration step; empty in a
workspace that links no library).

**`zotero_objects`**: one row per Zotero object the link knows, by its
key.

| Column | Meaning |
|---|---|
| `key` | The Zotero key (primary key) |
| `kind` | `collection`, `item`, `attachment`, `note`, `annotation`, `image` (a note's picture) |
| `page_id`, `block_id` | Where it is in Gamma: a folder (page `folders`), a page (`block_id` = the page id), a note region's root, an annotation block; `block_id` `''` for an annotation not shown yet |
| `slot` | `page` for the "Notes from Gamma" note; an ink item's slot among its block's items (`0`, `1`, …) or a split piece's (`0.1`) |
| `parent` | Zotero's parent key (`parentItem`; a collection's `parentCollection`) |
| `zotero_type` | `itemType` (or an annotation's `annotationType`) the bases were taken for |
| `version` | Zotero's version as last seen or written; `-1` while a create is unconfirmed |
| `state` | below |
| `zbase`, `gbase` | Zotero's projection as last seen or written; Gamma's as last synced ([Merging](#merging)) |
| `gsig` | A short signature of Gamma's side when `gbase` was taken ([Merging](#merging)) |
| `detail` | JSON, per kind (below) |
| `synced_at` | When the row was last written |

The states:

- `live`: in step;
- `pending`: Gamma's create was sent and not confirmed yet (`version` −1,
  `detail.sent` the projection sent; [Keys and identity](#keys-and-identity));
- `held`: an annotation block on its page that Gamma may not write now
  (`detail.reason`); Zotero's changes wait in its `zbase`, which is left
  as it was;
- `waiting`: something Zotero holds that Gamma cannot show or take yet: an
  annotation whose PDF is not here or differs, an attachment whose file
  cannot come, or whose Gamma file waits while the page's highlights are
  placed (`detail.file`), a note moved here whose region is not made
  yet;
- `trashed`: in Zotero's trash, Gamma's Recently deleted, or both. The row
  stays, so a block or page that comes back restores its object instead of
  making a new one;
- `rejected`: Zotero refused it (`detail.reason`; `detail.rejected` the
  signature it refused: it goes again once that changes), or a unit gave
  it up;
- `readonly`: another group member's annotation, pulled and never written;
- `skipped`: nothing Gamma makes of it (a note with no text, an
  annotation with no place on its page, a sticky note or text annotation
  with no text, one on a web snapshot or an EPUB), terminal at its version.

`detail` keys, by kind:

- every kind: `first` (a first link's or a force's pair, not decided
  yet), `adopt` (the side such a pair keeps once its first link or force
  is spent: [Finishing](#finishing)), `restore` (a backup restored over it), `sent`, `rejected`,
  `reason`, `gseq` (the change number the unit's own write gave the page:
  [The round](#the-round));
- item: `collections` (Zotero's collection keys as last read, trashed ones
  included), `declined_md5`, `adopts` (the standalone PDF an item was made
  for), `matched` (why a first link paired it);
- attachment: `md5` (Zotero's file as last synced), `gmd5` (Gamma's),
  `doc_id`, `fsig` (`[size, mtime_ns]` of the stored file when `gmd5` was
  read), `file` (why it waits), `size`, `filename`, `link_mode`, `made`
  (an attachment Gamma created), `trashed` (sent to Zotero's trash by a
  full storage), `copy` (a copy of Zotero's file another page holds),
  `keep` (keep Zotero's file before Gamma's goes over it), `theirs` (on a
  new attachment beside a file Zotero replaced too: that attachment, whose
  file is kept here before Gamma's goes), `beside` (on a new attachment:
  the PDF it went beside, `{key, md5, row, rows}`, [Uploads](#uploads)),
  `parent_to` (a standalone PDF's move under its new item, not sent
  yet);
- note: `lossy`, `rep` (the ids of the blocks the region represents, root
  first), `force` (send Gamma's lossy note anyway);
- annotation: `stroke_ids`, `look` (ink), `author` (another member's),
  `labels` (`zotero_annotations.LABELS` once its Zotero page label is
  settled: sent as pdf.js reads it, Zotero's own, or checked; 0 while the
  page of an annotation Gamma made is queued for the check,
  `zotero_sync_blocks.label_checks`), `box_check` (1 once the page of a
  `text` row whose box is no Zotero box was queued, `box_checks`);
- image: `name` (the stored upload);
- collection: `kept` (a folder left here while its collection is in
  Zotero's trash), `collateral` (trashed with its parent), `members` (the
  items whose bases named it, for a restore).

Indexes: `block_id`, `page_id`, `(parent, version)` (a parent's rows, and
the newest top-level rows for "When opened") and `(kind, state)` (every
round's own looks at the rows).

**`zotero_queue`**: what rounds still have to visit, worked highest
priority first (`zotero_remote.take`: `priority DESC, version DESC, id`,
walked by `idx_zotero_queue_order`).

| Column | Meaning |
|---|---|
| `id` | A Zotero key, `page:<page id>` (a Gamma page) or `file:<attachment key>` (a PDF transfer) |
| `kind` | `top`, `child`, `collection`, `page`, `file` |
| `version` | The version Zotero listed |
| `priority` | `OPENED_PRIORITY` (2^41) for a page someone opened and its PDF, `PAGE_PRIORITY` (2^40) for a Gamma page (and for the upload into an attachment Gamma just made), an object's version for Zotero's (newest first; a PDF transfer, its item's version or its attachment's, the newer), a child's version − `CHILD_OFFSET` (2^39), so its item's unit takes it first |
| `attempts`, `next_at`, `reason` | The backoff, and why it waits |
| `parent` | A queued child's `parentItem` once a round has read it (`set_parents`), `''` before; indexed `(parent, id)` for children |

`enqueue` upserts: an entry queued again keeps the higher version and
priority, and a newer version starts its backoff over.

**`zotero_state`**: the link's one row.

| Column | Meaning |
|---|---|
| `library` | The library these rows belong to; another library's are wiped |
| `version` | Every Zotero change at or below it has been listed (applied, queued, or terminal) |
| `local_cursor` | The local change feed's cursor ([collab.md](collab.md) "The change feed") |
| `labels` | `{label id: name}` as the last two-way round read them |
| `first` | `''`, or a first link in progress: `new` (a new workspace), `existing`, `notes` (notes access granted later) |
| `restored_at` | The last workspace restore over these rows (`ws_backup`) |
| `whole_version` | The version of the last listing from version 0 |

`sync_log` and `sync_conflicts` are the mirror's tables
([Conflicts and the log](#conflicts-and-the-log)). `remove_link` empties
all five.

### Properties the engine writes on pages and blocks

- A page kept with an item: `zotero_key` =
  `zotero_web.item_uri(library, key)`
  (`http://zotero.org/users/<id>/items/<KEY>`), the form the RDF export
  and import use. A note page: `zotero_key` and `zotero_note`, both its
  standalone note's URI. A page made from an item gets `meta` (with
  `source: "zotero"`), `bibtex` and `cite_key`.
- The root of a Zotero-note region: `zotero_note` = the note's URI.
- An annotation block from Zotero: `zotero_annotation` (its key),
  `imported_annot` (the key the embedded-annotation import gives the
  annotation Zotero would embed for it, so an RDF import adds nothing
  twice) and `annot_stripped: true`.
- `zotero_pdf` on a page whose item has a PDF Gamma does not hold:
  `{key, state, reason, size, filename}`, `state` `remote`, `queued`,
  `downloading` or `unavailable`, `reason` `linked`, `unavailable`,
  `files`, `quota` or `failed`. It goes when the PDF is attached
  ([PDFs](#pdfs)).
- `zotero_readonly` on a block Gamma will not write back: `lossy` on a
  note region's root (or a note page's root), `locked` on another group
  member's annotation, with `zotero_author`.

The row decides identity, never a property: a duplicated page or block
carries `zotero_key` or `zotero_annotation` but has no row, and is new
(the app's duplicate also drops `zotero_pdf`, `zotero_readonly` and
`zotero_author`). `remove_link` takes the marks off every page and block,
those in Recently deleted included.

## Keys and identity

Objects Zotero made keep Zotero's keys. An object Gamma creates gets a key
derived from where it is in Gamma, `zotero_web.object_key(ws, library,
kind, parent, block_id, slot)` (a SHA-256 of the seed spelled in Zotero's
key alphabet), and is created with `version: 0`, which Zotero refuses with
412 when the key exists. The seeds:

| Object | Seed after `ws, library` |
|---|---|
| An item for a page | `"item", "", page_id, ""` |
| An attachment for a page's PDF | `"attachment", item key, page_id, the PDF's MD5` |
| The "Notes from Gamma" note | `"note", item key, page_id, "page"` |
| An annotation item | `"annotation", attachment key, block_id, slot` |
| A collection for a folder | `"collection", parent collection's key, folder_id, ""` |

Putting the parent in the seed makes an object moved to another parent a
new object, which is what Zotero holds it as. A create retried after an
interruption names the same key, so it can never make a second object.

A **pending row** is written before its create is sent: `state pending`,
`version` −1, `detail.sent` the projection sent, `gsig` the signature it
was built from. The create is confirmed one of two ways:

- the listing names the key: the unit finds the row pending, takes
  `zbase = gbase = sent` (what Zotero held right after the create), and
  merges as for any row, so a change on either side since is decided as
  usual;
- a version-0 create answers 412 (`Outcome` `EXISTS`, Zotero's object
  read back): the same kind of object under the same parent (same
  `itemType`, `parentItem`, `annotationType`) is ours, a create that
  landed or one in Zotero's trash. It is brought back with a PATCH of what
  Gamma holds now and `deleted: false` (`_Unit._exists`). A page's item
  whose create had gone before (`_send_creates`'s `resend`) is confirmed
  through its unit instead, so what the person did to it in Zotero since
  survives. Anything else under the key gets a fresh `random_key()` and is
  created again.

Each round derives a pending or refused row again from Gamma as it is
(`zotero_sync_pages._resend`, `zotero_sync_gone.send_back`,
`Blocks._resend_note`, `Blocks._resend_annotation`, the collections'
`push_new`): the block gone
drops the row; a refused create goes again only once its signature
changed; otherwise the create goes again under the same key.

`random_key()` is also the key of an object Gamma keeps after Zotero
removed it for good: the edit that beat the delete goes back as a new
object under a key Zotero never held, never under the removed one (an
annotation whose derived key another row of the link holds gets one too).

What pairs a Gamma thing with a Zotero object is the row:

| Row kind | Gamma side |
|---|---|
| `item` | a page (`block_id` = `page_id`) |
| `attachment` | the page's PDF; a standalone PDF's row has no `parent` |
| `note` | a child note's region root; `slot page` with `block_id` = the page id for "Notes from Gamma"; a standalone note's note page |
| `annotation` | an annotation block, by `slot` among its items |
| `image` | a note picture's stored upload (`detail.name`) |
| `collection` | a folder of page `folders` |

A first link writes these rows for the pairs it matches before anything is
reconciled ([First link and force](#first-link-and-force)); after that
only the engine writes them.

## What syncs

| Gamma | Zotero | How |
|---|---|---|
| folders | collections | by key; name and parent; trash both ways, never a stripped membership ([Collections and folders](#collections-and-folders)) |
| labels | manual tags (type 0) | by name, ignoring case; automatic tags (type 1) stay in Zotero and go back unchanged, unless the link brings them (`auto_tags`) |
| a paper page (a record with a title) | a top-level regular item | the record field by field ([Items and pages](#items-and-pages)) |
| a page with a stored PDF and no record | a `document` item | only with `send_unrecorded`, after its metadata lookup had two minutes |
| a standalone-PDF page | a standalone PDF attachment | its title, tags and collections; a parent item once its record is a person's |
| a note page made from a standalone note | that standalone note | the note as a block-level diff |
| the page's record (`meta`, `cite_key`) | the item's fields | `zotero_fields`, per the item type's schema |
| the page's PDF | the item's primary PDF attachment | by MD5 ([PDFs](#pdfs)) |
| a region under a Zotero note's root | that child note | block-level diff ([Notes](#notes)) |
| the page's other top-level notes | one child note "Notes from Gamma" | after 60 s of quiet |
| a highlight, an area highlight, an ink group on a PDF page, a text box on a PDF page | `highlight` / `underline`, `image`, `ink` (one item per look), `text` / `note` annotations of the primary attachment | while both sides hold the same file ([Annotations](#annotations)) |
| a highlight's own note and the notes nested under it | the annotation's comment | as the RDF export's popup text |
| Recently deleted | Zotero's trash | both ways; Gamma never sends a DELETE |

Never synced:

- comment threads, AI chats, reading positions, preferences, page covers,
  search indexes (Zotero's full text included);
- Gamma's own note pages, notebooks and sheets (a text box on a sheet is
  no PDF annotation), recordings, link blocks, pictures placed on a PDF
  page or a sheet ([pictures.md](pictures.md));
- pictures in Gamma's notes (they are kept blocks; a picture that came
  from a Zotero note goes back as that note's picture);
- the item's other attachments (snapshots, second PDFs, linked URLs) and
  their annotations, annotations on EPUBs and web snapshots;
- an annotation's tags (Gamma has no place for them; a PATCH never sends
  `tags`, so Zotero's stay) and a passage's `nextPageRects` (Gamma shows
  the part on its first page);
- the item fields Gamma's record has no place for (abstract, URL, extra
  lines it does not write, other creators): `patch_for` writes only the
  fields that changed and leaves the rest as Zotero has them;
- saved searches, relations, Zotero's settings.

A key without notes access sees no note at all (Zotero leaves them out of
every read), so notes travel in neither direction while it lacks it: no
standalone note becomes a page, no "Notes from Gamma" is made.

## The round

`zotero_sync.sync_workspace(ws)` runs one round under the workspace's round
lock (`_clock.lock(ws)`); the clock calls `_held_round` with the lock
already held and never waits on it ([Clock and stream](#clock-and-stream)).
A detached link's round returns its status and does nothing. A round is
bounded: it takes no new unit after `ROUND_BUDGET_S` (60 s) or
`ROUND_UNITS` (50 units), ends normally, and asks the clock for the next
round at once while due work remains, so a first fill of thousands of
items is many short rounds and no request waits behind it for long.

`_round` runs these steps, each committing what it did before the next
starts, so a round cut off anywhere (a crash, a detach, an error) loses
nothing the next round cannot find again:

1. **Access** (`_access`). The workspace must still be a personal one of
   the link's owner (else 409 `not_connected`: the key is the owner's).
   The round's client is `zotero_account.client_for(owner)`, its heartbeat
   `_detach_check` ([Clock and stream](#clock-and-stream)). What the key
   may do in the library (`ZoteroClient.libraries`) is read at most every
   `ACCESS_TTL_S` (15 minutes) while the connection stays the same, and
   forgotten on any error and on the stream's `topicAdded` /
   `topicRemoved`. A library the key no longer reads is `library_gone`; a
   key that cannot write makes the round receive only (`r.mode`); a group
   renamed in Zotero renames `library_name`. Access newly granted since the
   last round: write or files starts the local feed over, so every page is
   looked at again (and files re-queues the PDFs that waited for file
   access); notes sets `zotero_state.version` to 0, the local cursor to ""
   and, unless a first link is still in progress, `first` to `notes`
   ([First link and force](#first-link-and-force)).
2. **The library's rows** (`_own_library`): rows of another library (a
   backup of another link restored here) are wiped and the workspace is
   matched again as a first link; a workspace restore since the last round
   is handled ([Backup restore](#backup-restore)).
3. **A force asked for** (`_start_force`), Zotero's full storage lifted
   (`quota_step`, [PDFs](#pdfs)), and a first link's pairs written before
   anything is listed (`first_match` for `first == "existing"`,
   `first_notes` for `"notes"`, both only while `version` is 0).
4. **Zotero's changes** (`_list_zotero`, below).
5. **Gamma's changes** (`_list_gamma`, below), on two-way rounds only: a
   receive-only round leaves the local cursor where it is.
6. **The queue** (`_work`): the collections pass (one unit of the
   budget), then the units.
7. **The record** (`_record`): the status and the next round.

### Listing Zotero's changes

`zotero_remote.list_changes(client, library, since)` is Zotero's delta
protocol ([research note](../research/zotero-sync.md#what-the-web-api-offers)):

- With `since` above 0, one small request first: `items?limit=1&format=versions`
  with `If-Modified-Since-Version: since`. Zotero's 304 says nothing
  changed, and the round skips the listing (the queue is still worked).
- Otherwise `{key: version}` of the collections, of all items and of the
  top-level items that changed after `since` (`format=versions`, unpaged,
  `includeTrashed=1` for items: Zotero lists a trashed collection whatever
  the query), and `/deleted?since=` (what was removed for good). Children
  are all items minus the top-level ones; all items are read before the
  top-level ones, so an item made between the two reads is never taken for
  a child.
- The listing's version is the first answer's (the 304 probe's, or the
  collections answer's from version 0). A change made while the listing
  is read is in it and is listed again next time, so the library moving
  mid-walk loses nothing.

`_list_zotero` then applies what Zotero removed for good: it drops those
keys' queue entries in a commit of its own, then runs `collections_gone`
and `items_gone` (what they queue again, a folder edited while its removal
was decided, stays queued), and finally queues the rest
(`zotero_remote.queue_changes`) and moves `zotero_state.version` to the
listing's, `whole_version` too for a listing from version 0. The listing,
not the application, moves the version: what is listed is applied, queued,
or terminal at that version.

`queue_changes` leaves out a key whose row is at the listed version already
(the link's own write, which recorded the version Zotero answered, or a row
kept terminal there), unless the row is to be decided afresh
(`detail.first`). Collections and top-level items are queued by their
version; children at their version − `CHILD_OFFSET`, after every item, so
the item's unit reads them and takes their entries off the queue.

### Listing Gamma's changes

`_list_gamma` walks the local change feed from `zotero_state.local_cursor`
in-process (`routers.sync.changes`, `FEED_BATCH` 1,000 entries a read, at
most `FEED_READS_MAX`, 200, reads a round) and queues a `page:<id>` entry for
each page listed, deleted pages and the `folders` and `labels` trees
included. It also queues every page carrying a label whose name is not the
one `zotero_state.labels` recorded, so a label renamed here (a pulled one
too) reaches each item's tags. It also queues, once, each page with a live
row of an annotation Gamma made whose page label is neither settled nor
queued (`zotero_sync_blocks.label_checks`, which marks those rows queued,
`detail.labels` 0, and settles the rows of Zotero's own annotations), so a
label a create sent undecoded is corrected without an edit; such a page is
queued, not made due, so one whose notes wait to be still keeps waiting.
With them it queues, once, each page with a live or read-only `text` row
whose block's text box lacks the `zotero` key (`box_checks`, which marks
those rows `detail.box_check`), so a box stored without the key gains it
without an edit ([Text boxes](#text-boxes)).
The scan reads the `detail` of every live annotation row each two-way
round (about 25 ms for 50,000 rows). A page changed again while its "Notes from
Gamma" waited to be still is made due at once (`zr.wake`): only that note
waits for quiet, never the page's record, highlights or Zotero's notes.
The cursor moves to the feed's, unless a settings change started it over
meanwhile; a page whose reconcile fails stays queued, so moving the cursor
loses nothing.

**The change-feed skip.** Without it, every page the link writes would be
listed again by the next round's feed and cost a unit to find nothing to
do. A unit that settles its page whole with its own write records the
page's change number (`page_changes.seq`) as that transaction left it on
the page's own row, `detail.gseq` (`_Unit._own_change`). On an incremental
listing `_list_gamma` passes over a page whose newest change is still that
number. The unit records it only when nothing is left for later: a
two-way round, no Zotero write queued by the unit (Zotero's answer could
send the page back to be decided), a page made here or written under the
compare-and-set in that very transaction (not a trash or a restore), and
not a standalone PDF whose record now earns a parent item. The page's own
row is the item's, or a standalone PDF's or note's while no item holds
the page. A person's edit since moves the change number on, and a feed
started over (a settings change, a force, a restore) looks at every page.
Labels the unit creates are recorded in `zotero_state.labels` in the same
transaction, so making them never reads as a rename.

### The queue and its units

`_work` makes the collections pass first when one is due (a collection
entry due, a `folders` or `labels` tree listed, or a collection create not
confirmed on a two-way round; [Collections and folders](#collections-and-folders)).
A pass that fails for a reason that does not stop the round is recorded as
the round's error and its entries are passed over until the next round.

Then it takes due entries of the unit kinds (`top`, `child`, `page`,
`file`), highest priority first, and runs one at a time (`run_unit`):

- `top` (`zotero_sync_items.unit_item`): the top-level item read whole with
  its children, reconciled with its page;
- `child` (`unit_child`): a child listed alone (an attachment, a note, an
  annotation, a note's picture). Its top-level item is found by the rows
  or read up the parents (`_top_of`, four levels at most), and that item's
  unit runs, which reads every child. An annotation whose attachment is no
  PDF (a web snapshot, an EPUB) gets a `skipped` row at its version and
  costs no unit of its item; one whose parent is gone is passed by (the
  removal is listed too);
- `page` (`zotero_sync_pages.unit_page`): a Gamma page
  ([Items and pages](#items-and-pages));
- `file` (`zotero_sync_pdfs.unit_file`): a PDF transfer ([PDFs](#pdfs)).

A unit answers `HELD` (nothing done now: passed over for the rest of the
round, at no cost: a page waiting for its metadata lookup), `SETTLED`
(nothing to do: a page the feed listed that is as it last synced, at no
cost) or anything else once done; after each counted unit the queue is
read afresh, since a unit may have taken other entries off it. The round
stops taking units when its link is detached meanwhile.

**Reads by key.** An item's unit reads the item through `read_ahead`: it
and the next due top-level entries are read by key together,
`READ_AHEAD` (50) a request, and kept on the round (`Round.read_ahead`)
for their units; one read before a newer version was listed is read again.
Its children come one of two ways (`zotero_sync_reads.read_unit`):

- **with the item, by key**, for a fill: an item queued by a listing from
  version 0 (its entry above 0 and at most `whole_version`) with no row,
  no row under it, none of its queued children with a row, and every
  queued child's parent read. The queue keeps each child's `parentItem`
  once read (`set_parents`, re-read when the child is listed at a newer
  version); the parents not read yet are read by key, at most
  `CHILD_READS` (5,000) a round, while there are at most
  `CHILDREN_PER_ITEM` (30) unread children per item they may serve (past
  that, items of hundreds of annotations, paged child lists cost fewer
  requests). The item's family is read by key together with the families
  of the next items read ahead, whole families up to `CHILDREN_AHEAD`
  (250) children, in the order a child list gives (`_family`). The
  `whole_version` guard matters: an item first seen in Zotero's trash
  leaves no row but takes its children's entries, and after its restore
  only a child list finds those children;
- **child list after child list** otherwise (`zotero_remote.descendants`:
  `items/<key>/children?includeTrashed=1`, sorted by `dateAdded`, paged,
  into each PDF attachment for its annotations and each note that shows a
  picture). That covers items with rows, items Zotero made after the
  listing, Gamma-page units, re-queued units, and the re-read after a PDF
  arrives.

A key the listing named that a read by key does not return is checked
against `/deleted` (`absent`): removed for good, it is applied as
`items_gone`; else the unit fails and is tried again.

**Backoff.** A unit that raises backs its entry off (`zotero_remote.backoff`:
30 s · 2^(n−1), at most 6 h). After `MAX_ATTEMPTS` (8) the entry leaves
the queue and `_give_up` makes its row terminal at the version it was
listed at (`rejected`; a file's `waiting` with `detail.file = "failed"`;
a `page:` entry leaves no row), with a `zotero_rejected` conflict, "Gamma
could not apply this after 8 tries: …". A Zotero change lists it again
(a page, its next edit). An error whose kind is in
`STOPS_ROUND` (`offline`, `auth`, `not_connected`, `library_gone`, `busy`)
ends the round instead, the unit untouched: no unit is to blame.

**Decided again.** A unit whose page changed under its decision (the
compare-and-set, [Writing to Gamma](#writing-to-gamma)) or whose object
Zotero changed as it was written (412) writes nothing more, goes back to
the queue and is passed over for the rest of the round
(`Round.decide_again`); the round asks for a follow-up `BUSY_RETRY_S`
(5 s) later.

### Finishing

`_record` writes the status: the counters, `backlog` (the queue's size),
`waiting` from the rows (`_waiting`), `mode`, `access`, `username`,
`last_sync`, and `last_error` / `error_kind` from the round's first error
(a clean round clears them).

A first link's, a force's or a restore's state (`zotero_state.first`,
`status.adopt`, `status.forced`) is **spent** after a round with no error
that leaves nothing queued but PDFs held back by this server's full
storage (`_spent`: entries whose reason is `STORAGE_WAIT`, queued again
every `STORAGE_RETRY_S` for as long as the storage stays full, which may
be days; Zotero's full storage queues nothing, its rows wait on `quota`).
Anything else still queued keeps it going (notes waiting for the page to
be still, a backoff until `MAX_ATTEMPTS`, a PDF waiting for its
highlights to be placed), so the pill keeps reading "First sync", "Force
pull" or "Force push" and the first link's pairing rules stay in effect
for what arrives meanwhile (folders matched by path while `first` is
`existing`, child notes by their words while it is `notes`). Once spent,
`zotero_state.first` goes, and `status.adopt` and `status.forced` go
unless a newer one was asked for while the round ran. A pair still to
decide (a PDF full storage holds, a record Zotero refused) keeps the side
it was paired under: each row still marked `first` gets `detail.adopt`
(unless it has one), which items, notes, annotations, PDFs
(`zotero_sync_pdfs.pair_side`, the item's row before the attachment's)
and collections read before the round's `adopt`. Such a pair is decided
later as its first link or force would have decided it, never by the
`theirs` a round without `adopt` falls back to; a force drops every
`detail.adopt` as it marks the rows ([Force](#force)).

A clean round settles the clock's dirty mark
(`_clock.settle(ws, started)`): every write made before the round started
went out with it. Then the next round: at once while due work remains
(`status.fill` carries the fill's counts to it, so the pill counts the
whole fill), `BUSY_RETRY_S` later for what was decided again, else when
the queue's soonest wait ends (`next_wait`: a backoff, a page's notes
waiting for quiet). Each round that decided differences for a first link
or a force writes a summary line to the log ("replaced here: 212
records, 31 notes", or "replaced there" when Gamma's side was taken,
`_decided_note`), so one that runs over many bounded rounds writes
several.

The status the frontend reads as **pending local edits**
(`zotero_sync.pending_local`): a two-way link whose key writes, with a
write no clean round has settled yet, or a page entry queued and due (one
held back, a page whose notes wait for quiet or a backoff, does not
count).

## Merging

### The two bases

Every synced object is compared as two **projections** in Zotero's terms:
**Z**, Zotero's object as fetched, and **G**, Gamma's thing as it would be
sent. The row keeps `zbase` (Z as last seen or written) and `gbase` (G as
last synced). For each field: Zotero changed it when Z left `zbase`,
Gamma changed it when G left `gbase` (`zotero_merge.merge`):

- only Zotero changed it: **applied** to Gamma, by a writer of that field
  alone, never a rewrite of the whole thing;
- only Gamma changed it: **pushed**, that field alone;
- both changed it to the same value: nothing is written, the bases move;
- both changed it apart: a text field the merge knows (a record's `title`,
  `venue` and `publisher`, `zotero_sync_items.RECORD_TEXTS`; an
  annotation's comment, in Gamma's terms through `comment_in`, a text
  box's through `text_box.markdown_of`, but not for a block with nested
  notes or an ink group) is merged with `textmerge`, a clean
  merge going both ways (`merged_text`), and a text that already holds the
  other side's edit is taken as it is. Otherwise one side wins, `prefer`:
  Gamma's in an ordinary round (both sides end with Gamma's, a conflict row
  keeps Zotero's), Zotero's for a first link adopting Zotero's, a force
  pull, or another member's annotation;
- a field missing from a base (a new row, a first link's disagreement)
  reads as changed;
- a version bump that changed no field (a tag on an annotation, a field
  Gamma does not map) only moves the row's `version`.

The set-valued fields (`tags`, `collections`, `zotero_merge.SETS`) merge
member by member: a member one side added or removed is added or removed
on both, unless both changed it apart, where `prefer` decides.

Why two bases and not one: every mapping between Gamma and Zotero loses
something one way (a quote Zotero clips at 7,500 characters, a comment's
`<sub>`, a turned text box, a stroke's pressure, a note's paragraph shape).
With one base, Gamma's lossy copy of Zotero's value would read as Gamma's
change and be written over Zotero's. With two, each side is compared only
with what it held itself.

`zotero_merge.bases_after` gives the bases a unit records once its writes
are done:

- a field applied: `zbase` takes Z's value, `gbase` Gamma's own reading of
  the result (`G_after`, the page or block projected again inside the
  unit's transaction, after its ops), so the mapping's loss never reads as
  Gamma's change;
- a field pushed that landed: `zbase` takes the saved object's projection
  (`successful[i].data`), `gbase` G as decided; the row's `gsig` is the
  signature the decision was made on, never one re-read at write time;
- a field pushed that did not land: `zbase` takes Z, `gbase` keeps its old
  value, so the next round sees Gamma's change and sends it again. When
  Zotero had changed it too (Gamma's won a conflict), `gbase` is None:
  whatever Gamma holds then goes, even a change taken back, which Zotero's
  newer value would otherwise outlast unseen on both sides;
- any other field: both bases take the values now.

A unit's local transaction writes `version`, `zbase` and the bases of what
it applied; the fields it pushes move in `gbase` only when
`Writes.flush` reports them landed (`_Unit._record`, `Blocks.record`,
`_Folders.record`).

**Signatures.** `gsig` is Gamma's side as a short hash when its base was
taken: a hash of G for records, standalone PDFs and note pages, a hash
of the name and parent folder for a folder, `zotero_annotations.block_sig`
for an annotation block (its note, colour, quote, place and nested notes),
the region's HTML hash for a note. An annotation block whose signature is
as synced is not projected again (`_annotation`, `blocks_dirty`: reading
its place needs the PDF page), and a write Zotero refused goes again only
once the signature changed (`detail.rejected`).

### The projections

| Object | Z | G |
|---|---|---|
| Item | `item_projection(data)`: `{type: itemType, fields: {title, authors, year, venue, volume, pages, doi, arxiv_id, publisher, isbn, kind, cite_key}, tags: [folded names], collections: [live keys]}` | `page_projection(meta, cite_key, title, item_type, labels, folder keys)`: the record normalised as Zotero would hold it (`zotero_fields.normalized`: a value the type has no field for is absent on both sides) |
| Standalone PDF | `standalone_projection`: `{title, tags, collections}` (the file's name for an untitled PDF) | the page's title, labels and folders; its record is Gamma's alone and never travels |
| Standalone note | `{tags, collections, note: hash of the HTML}` | the same of the note page, its note as Gamma would send it |
| Child note | `{note: hash}` of Zotero's HTML | `{note: hash}` of the region's HTML |
| Annotation | `zotero_annotations.projection`: `{type, color, comment, text, position}` | the same of the block's item (`to_items`) |
| Collection | `{name, parent}` (`collection_projection`) | the folder's name and its parent folder's collection key (`folder_projection`) |

Tags compare as names trimmed and lower-cased (`fold_tag`, as Gamma finds
a label); automatic tags take part only with `auto_tags`. Collections take
part only while their row is live and their folder exists
(`collection_map`): a trashed collection is in neither side of any item's
merge ([Collections and folders](#collections-and-folders)). An
annotation's position is canonical (rounded to hundredths of a point,
keys other clients added left out); its sort index and page label are
derived from it and are not part of it.

The page label is never compared: Zotero labels its own annotations its
own way (Zotero 9's reader from the page numbers printed on the pages when
they contradict the PDF's labels, the offset of the last label a person
corrected, a label typed), so Gamma sends one with a create, with a change
of page, and once over the label its own create carried when that reads
otherwise now ([Kinds and positions](#kinds-and-positions)).

### Retypes and kindless records

An item's type is a field: `type` in both projections. Gamma's side is
`zotero_merge.write_type(meta, item_type)`: the item's own type while the
page reads as the kind the item does (a `blogPost` stays one on a page of
kind `document`, since Gamma's nine kinds cover fewer types than Zotero's),
else the page's kind, so a kind changed on either side shows as a change.
G is compared with `gbase` against the type `gbase` was taken for (the
row's `zotero_type`, `_Unit.G_synced`): a retype in Zotero never reads as a
change of Gamma's page. A retype decided for Zotero goes as a PUT of the
whole retyped item (`zotero_fields.patch_for` answers `replace`;
`zotero_fields.retype` moves the fields as Zotero does), with
`If-Unmodified-Since-Version`; the row's `zotero_type` follows the saved
item.

A record that names no kind reads as its linked item's type, not as what
its fields imply: `item_types.infer` picks only a new item's type (and the
type of a `document` placeholder after a lookup), so a field pulled or
edited never retypes an item.

### Restored and first-link bases

Two marks change how a row's bases are read. `detail.first` (a first
link's or a force's pair) replaces them with `zotero_merge.first_link_base`
([First link and force](#first-link-and-force)); `detail.restore` (a
backup restored over the row, [Backup restore](#backup-restore)) makes
what Zotero changed since the backup read as changed here too, so the
restored side wins it and Zotero's value stays in a conflict.

## Collections and folders

Collections and folders pair by key, so two collections Zotero holds under
one name stay two folders. The projection is `{name, parent}`, the parent a
collection key (`''` at the top); trash is a state of the row, not a
field. One **collections pass** a round (`zotero_sync_folders.collections_pass`)
decides them all, before the units, so a page is never filed in a folder
that is not made yet:

1. The due collection entries are read by key (`fetch_collections`), and
   the parent collections they name that the link does not know, a level
   at a time (`PARENT_READS_MAX`, 16, reads at most). A key Zotero did
   not answer is checked against `/deleted`.
2. `_Folders.pull` reads what Zotero holds, parents first: a new collection
   becomes a folder last under its parent's folder (`folder_insert`, client
   `zotero`); one Zotero holds under a folder's derived key and the same
   parent is that folder's, confirmed; a pending create Zotero now lists is
   confirmed; a collection trashed in Zotero goes here (`_trash_here`); one
   restored comes back (`_restore_here`).
3. `_Folders.merge` decides every live row field by field: Zotero's rename
   or move as folder ops, Gamma's as a PATCH (`name`, `parentCollection`,
   `false` at the top). A folder deleted here sends its collection to
   Zotero's trash, unless Zotero changed it meanwhile: then the folder
   comes back with its members (a `zotero_deleted_edited` row).
4. `_Folders.push_new` (two-way rounds): folders without a row are created
   under their derived keys, their rows written pending first; pending and
   refused creates are derived again; a folder that came back here (an
   undo) restores its collection in Zotero.
5. `apply_local` writes the folder ops and the rows in one transaction,
   under a compare-and-set on the stamps of every folder the pass read
   (`Changed`: nothing is written and the pass is decided again soon),
   then deletes each folder a Zotero trash removes (`zotero_local.delete_folder`,
   `ops.delete_tree` on `folders`, which takes it off the pages filed in
   it). The follow-ups go in the same transaction (`follow_ups`): the items
   of restored collections are queued, so their pages are filed again, and
   the pages filed in folders whose collection key was confirmed or
   changed are queued, so their items join it.
6. Zotero's writes are flushed and their outcomes recorded: a 412 lists the
   collection again at once, a transient failure backs the tree off, a
   refusal makes the row `rejected` with a `zotero_rejected` row.

**A trash never strips membership.** Deleting a folder here refiles its
pages, and Zotero's trash keeps the collection's members. When a
collection's row turns `trashed` (Zotero's trash, or Gamma's delete sent),
`_forget_collection` takes its key out of both bases of every item row,
and `collection_map` leaves it out of both projections: the refiling the
folder's removal causes never reads as a change on either side, so no
item loses the collection in Zotero. The members are kept on the row
(`detail.members`); a restore reads Zotero's membership as Zotero's
addition and files the pages again. Only a removal for good drops the key.
A folder with no live collection behind it stays where a page is filed
when Zotero's collections are applied, and a collection Gamma has no row
for stays in an item's collections when Gamma's are sent.

**A trashed collection whose folder is shared or kept on disk** (a share
link, or a desktop folder link, `_kept_folders`) stays here: deleting it
would end its share links and invitations for good, and a restore in
Zotero could not bring them back. Its rows turn `trashed` with
`detail.kept` (its subfolders' rows too, `collateral`), the folders and
the pages' filing stay as they are, and a "kept here" log line says why.
A Zotero restore makes the rows live again with the bases they kept, so
what either side changed meanwhile is merged; emptying Zotero's trash
removes an unchanged kept folder as any removal for good does. A restore
of a folder that was deleted gives it back its old block id when nothing
holds it, so what names the folder by id (a desktop folder link, a
reading position) finds it again.

**Removed for good** (`collections_gone`, from `/deleted`): a folder
unchanged since it last synced goes, with its subfolders, under a
compare-and-set on their stamps; one renamed or moved while the removal
was decided stays queued and is decided again by a later pass. A folder
changed here since it last synced stays, and becomes a new collection
under a fresh key, its pages queued so their items join it.

## Items and pages

### An item's unit

`zotero_sync_items._Unit` is one top-level item read whole with its
children (or a standalone PDF, or a standalone note) against the page it
is kept with. `reconcile` runs it start to end:

1. **Read** (`read`): the rows of the item and its children, the page's
   rows, the page (`page_side`: its root, labels, folders, stored PDF,
   compare-and-set stamp), its notes and annotations (`Blocks.read`). An
   item with no row whose attachment is a page's PDF while that page has
   no item (a standalone PDF that Zotero's Create Parent Item or Retrieve
   Metadata put under a new item) is bound to that page instead of making
   a second one (`_parentless_page`). Notes and annotations Zotero moved to
   this item from another (Merge Items, a note dragged across) move here
   with their blocks' subtrees first (`_take_children`,
   `zotero_local.move_block_across`); one whose block cannot move is
   brought from Zotero instead.
2. **Decide** (`decide`): the record, tags, collections and trash
   (`_decide_item`), then the PDF (`UnitPdf._file`, [PDFs](#pdfs)), then the
   notes and annotations (`Blocks`, [Notes](#notes),
   [Annotations](#annotations)).
3. **Write** (`write`): Gamma's side in one transaction under the
   compare-and-set (False when the page changed: the unit goes back to the
   queue with nothing written on either side), then Zotero's
   ([Writing to Zotero](#writing-to-zotero)), then each write's outcome on
   its row (`_record`).
4. **Finish** (`finish`): the entries done, the transfers and follow-ups
   queued, the log lines and conflict rows written after every commit. The
   page's own queue entry stays as it is (its backoff too) while the page
   holds what only its own unit does: an item create not confirmed or
   refused, a standalone PDF's move under its new item, a standalone PDF
   whose record now earns a parent item (`_page_work`).

**The record.** Zotero's changed fields go into the page's `meta` through
`zotero_local.record_op`: only those fields; `source` stays the page's
(`zotero` for a page that had none); `cite_key` pinned or unpinned; the
BibTeX entry built again; the stale slide citation and a failed lookup's
`meta_error` dropped. The page's title follows the record's while it is
the old record's title, automatic or empty. Gamma's changed fields go as
`zotero_fields.patch_for`: a PATCH of the changed fields where the item's
type has them, the other creators, a full date in Gamma's year and Extra's
other lines left as they are; a PUT for a retype.

**Tags and labels.** Zotero's tags come in as labels by name (new labels
are made in the same transaction, `zotero_local.filing_ops`), spelled as
the page's labels spell them, else as Zotero does. A PATCH of `tags`
(`zotero_merge.tags_for_push`) sends back every tag Zotero has that stays
as Zotero has it, with its type; every automatic tag stays unless the link
brings them and Gamma removed one; a new name goes as `{"tag": name}`,
Zotero's form of a manual tag.

### Pages Zotero has no item of

`zotero_sync_pages.unit_page` takes a `page:` entry. On a receive-only
round it takes the entry off the queue and does nothing more (a switch to
two-way starts the local feed over). A
page the link keeps is reconciled through its item's unit when it differs
from what last synced (`_page_dirty`: its record, labels or folders, its
trash state, its PDF, its notes and annotations, a pair not decided yet);
else it is `SETTLED`. A page with no row becomes an item when
`zotero_merge.wants_item` says so:

- a **paper**, a page whose record has a title: a regular item of
  `write_type`;
- a page with a stored PDF and no record: it waits while its metadata
  lookup may still bring one (no `meta`, no `meta_error`, made less than
  `LOOKUP_WAIT_S`, two minutes, ago: `HELD`, queued again then), then goes
  as a `document` when the link has `send_unrecorded`, else stays in
  Gamma;
- anything else (a note page of Gamma's, a notebook) stays in Gamma.

The creates go together (`_create_items`): the other due pages that become
items now join this one's, `READ_AHEAD` at most. Each page's item row is
written pending first, the items sent in one `Writes` (50 a request,
`_send_creates`), and then, under the keys they landed with, each page's
PDF as a new `imported_file` attachment when PDFs may go
([PDFs](#pdfs)), sent together too and uploaded by the file queue. Each
page's rows, its `zotero_key` and its entry are written in one
transaction of its own (`_create_written`). The item's body is
`zotero_fields.fields_from_meta` (named by the page's title when the record
has none), the labels as manual tags and the folders' collections.

### Standalone PDFs and notes

A standalone PDF attachment in Zotero becomes a **standalone-PDF page**:
its title (or file name), tags and collections travel, its PDF comes as
any item's, and the record a lookup gives it stays Gamma's. Once its
record is a person's (`meta.source == "manual"` or `meta.user_verified`,
with a title: `zotero_merge.standalone_gets_parent`), the page's unit makes
a parent item from it (`_create_item(parent_of=…)`). The new item's row
names the PDF (`detail.adopts`), the PDF's row names the move
(`detail.parent_to`), and `_send_move` moves the attachment under the item
as Zotero's Create Parent Item does: its collections go to the item, its
tags stay. The move goes only at the version the attachment last synced
at, so a change made in Zotero meanwhile comes into the page first. Until
it lands, no unit of the item takes the PDF for one to unbind or send
again (`moving_pdf`). A standalone PDF never gets an attachment of its
own.

A child attachment Zotero made standalone (dragged out of its item) gets a
page of its own; its old page keeps its file. An attachment that is no PDF
stays in Zotero.

A standalone note becomes a **note page** (`zotero_key` = `zotero_note` =
the note's URI): its first line the title, the rest its blocks (the RDF
import's rule, `note_page_parts`). Its note, tags and collections merge
like a record's fields (the note by its HTML hash; a first link agrees
when their words do), the note pulled as a block-level diff into the page
([Notes](#notes)). Gamma's own note pages never go to Zotero.

### Trash and removal

Gamma's Recently deleted and Zotero's trash stand for each other; an edit
beats a delete both ways ([home_library.md](home_library.md#recently-deleted)):

| The page… | Zotero's item… | What happens |
|---|---|---|
| live | trashed, page unchanged since it synced | the page to Recently deleted (`ops.trash_page`); what Zotero changed with the trash keeps reading as changed (its base stays), so a restore on either side takes it |
| live, changed here since it synced (or restored from a backup) | trashed | kept, and the item restored in Zotero with Gamma's changes; `zotero_deleted_edited` |
| live, after coming back here (an undo) | trashed | the item restored in Zotero |
| in Recently deleted | live, unchanged | the item to Zotero's trash |
| in Recently deleted | changed in Zotero since (a field, a note, an annotation, its PDF, a new child) | the page restored here (`zotero_deleted_edited`) |
| in Recently deleted | restored in Zotero after both were in their trash | the page restored here |
| purged from Recently deleted | live, unchanged since it synced | the item to Zotero's trash; the link never removes an item for good |
| purged | restored or edited in Zotero since | the page made anew from the item, its rows pointed at it |
| either | both in their trash | the row `trashed`; Zotero's changes keep reading as changed for whichever side restores it |

On a receive-only round, a page live here whose item is in Zotero's trash
takes Zotero's changes but keeps its row trashed, so the first round that
may send brings the item back with Gamma's changes. Before a page goes to
Recently deleted with its item, the notes and annotations Zotero moved to
another item move to that item's page first (`_rehome`).

**Removed for good** (`/deleted`, `zotero_sync_gone.items_gone`): a page
unchanged since it last synced goes to Recently deleted, its rows with it,
under a compare-and-set on the page. A page changed here since (a
standalone PDF's page by its file too) stays: the rows of the removed
object go (`_rows_of`: the page's item and attachment rows, the object's
own row, its note's pictures' rows), and the page goes back to Zotero
under a fresh key as `_goes_back` decides, which its
`zotero_deleted_edited` row says ("Zotero deleted this note for good after
it was changed here: it goes back as a new note"):

- an item's page becomes a new item (a pending row with nothing sent,
  created as any page's item by `zotero_sync_pages._resend`);
- a note page becomes a new standalone note while the key reads notes,
  else it stays here, unlinked;
- a standalone PDF's page becomes a new standalone PDF while the link
  sends PDFs without a record (`send_unrecorded`) and PDFs may go
  (uploads on, the key's file access); else it is from then on a page like
  any other of Gamma's, which becomes an item only as `wants_item` says (a
  paper does; anything else stays here, unlinked).

A note page or a standalone PDF's page goes back through
`zotero_sync_gone.send_back`, which the page's unit runs for a pending or
refused row that is not an item's. The row holds what is sent
(`detail.sent`) before the create goes, so a create whose answer was lost
is confirmed by the listing that finds it, and a 412 naming an object of
the same type with no parent is Gamma's own create, confirmed by its unit;
another object under the key gets a fresh one. A refused create is a
`zotero_rejected` row and goes again only once the page changes (a note
longer than `NOTE_MAX` is refused before it goes). It waits, sending
nothing, while the page is in Recently deleted, the key lacks notes access
or PDFs may not go (it goes once the page or the link changes), and while
Zotero's storage is full (the page's entry held until `quota_until`); a
page gone for good, or a PDF page that lost its PDF, takes the row with
it. Once Zotero holds the object the row is live, the page's `zotero_key`
(and a note page's `zotero_note`) names it, a PDF's upload is queued, and
the log says "created there". A note page sent back takes its pictures'
rows with the removed note: the pictures stay as Gamma's blocks.

An attachment that was a page's PDF leaves the page its file, Gamma's alone; its item decides its
PDF again, and a file the page holds unchanged is not sent back
(`detail.declined_md5` on the item row) until Gamma's file changes. Notes
and annotations Zotero removed for good go through
`zotero_sync_blocks.children_gone` ([Annotations](#annotations)).

## PDFs

### The primary attachment and the same file

A page has one PDF; a Zotero item may have several attachments. The
page's PDF is the item's **primary** attachment, as Zotero's reader picks
it (`zotero_merge.primary`, after `Zotero.Item.getBestAttachments`): among
the item's attachments that are not in the trash, of a file link mode
(`imported_file`, `imported_url`, `linked_file`) and `application/pdf`,
the one whose `url` is the item's, then the oldest (`dateAdded`), then the
smallest key. The attachment bound to the page stays its PDF while it
still qualifies; when it is trashed, removed or moved to another item, the
primary is chosen again. The item's other attachments never travel.

The two files are one when their **MD5s** are equal: Zotero's attachment
carries the MD5 of its stored file, and Gamma reads its own. The
attachment row keeps both as last synced (`detail.md5`, `detail.gmd5`),
with the stored file's `[size, mtime_ns]` (`detail.fsig`), so no round
reads a whole library's PDFs again: Gamma's MD5 is taken from the row
while the file's size and time are unchanged, and otherwise read only when
a decision asks for it (`zotero_sync_reads._FileMD5`;
`zotero_annotations.file_md5` caches by path, size and time, never by doc
id, since stripping embedded annotations rewrites a stored file in place).
Annotations travel only while the two MD5s are equal
([Annotations](#annotations)).

### The plan

Once the record is decided, the item's unit decides the PDF
(`UnitPdf._file`, `_file_plan`), and the file's own unit decides it again
on what both sides hold when the transfer runs (`unit_file`):

| Plan | When |
|---|---|
| `bind` | both hold the same file: the row records it |
| `fetch` | Zotero's file becomes the page's: a page without a PDF, a file replaced in Zotero and not here, a primary that changed, a first link's or a force's pair taking Zotero's (or keeping Gamma's over a file whose annotations Gamma cannot move) |
| `wait` | Zotero's file cannot come, with its reason; or Gamma's changed file waits while the page's highlights are being placed in it (`placing`, below) |
| `upload` | Gamma's file goes over Zotero's in place (replaced here, not in Zotero) |
| `new` | Gamma's file goes as a new attachment: the item has no PDF attachment at all, or Gamma's file cannot go in place (below) |
| `both` | both replaced: Gamma's goes over Zotero's in place, and Zotero's is kept here for a `zotero_file` conflict |
| `both_new` | both replaced, and Gamma's cannot go in place: Gamma's goes as a new attachment beside Zotero's, which stays in its own attachment, and the upload keeps a copy of Zotero's file here for a `zotero_file` conflict, or, when Zotero cannot hand it out, writes that conflict resolved, for the record ([Uploads](#uploads)) |
| `park` | the files differ and neither moves (a receive-only round, uploads off, a file Zotero Storage does not serve); the annotations wait (`WAIT_DIFFER`) |
| `kept_out` | the page let its PDF go (its row recorded one): Zotero's comes only when asked |
| `unbind` | the row names no primary any more |
| `none` | nothing to do |

Gamma's file goes **in place** only when PDFs may go (`may_send_files`: a
two-way round, `upload` on, the key's file access), the attachment is a
stored one (`imported_*`), the page's highlights are not being placed in it
(no relocation job running for the page and file, `_relocating`), and
every live annotation Zotero holds under the attachment can move onto
Gamma's file with it (`_unmovable`): it has a row Gamma may write (none of
`_UNMOVABLE`: `readonly`, `rejected`, `waiting`, `skipped`) and a block
nothing holds (`hold_reason`: not being placed, dashed or lost). A `held`
row is judged by its block as the page reads now, since every annotation
of a PDF replaced here is held while the two files differ: it moves once
it synced before (a `zbase`), is the user's own (no `detail.author`) and
its block is an annotation block nothing holds. A file Zotero Storage does
not serve (a linked file, WebDAV) gets no second PDF beside it: the plan
parks. A standalone PDF never gets an attachment of its own.

**While the highlights are placed.** Replacing a page's PDF here marks its
highlights "being placed" (`pdf_position.doc_id`) and starts their
relocation ([highlight_relocation.md](highlight_relocation.md)); the
link's round comes 3 s after the edit, before the job has ended. While
the job runs, Gamma's changed file goes neither in place nor beside: the
plan is `wait` with the reason `placing` (`FILE_PLACING`), the row
`waiting` with `detail.file = "placing"` (counted in
`status.waiting.files.placing`; a first link's or a force's pair keeps
its `first` mark), and its file entry held for `PLACING_RECHECK_S` (30 s,
"its highlights are being placed"). However the job ends, written or not,
`routers/pages.py` calls `relocation_ended` once the job reads as ended
(`jobs.start`'s `ended`, after its row and the live set say so; told as
the run returned, a round in between found the job still running and
held the PDF the 30 s again): the
page's PDFs that waited for it are due again, so is the page's item while
annotation rows of it are held or wait (a highlight the run left where it
was reads as last synced, so no change of the page lifts the hold the
replacement put on its row), and the link's round is asked for after the
clock's quiet time. The 30 s hold is the fallback. Only then is it asked
whether the annotations can move: once every highlight is placed,
Gamma's file goes in place over Zotero's, and so does a force push's or a
first link's Keep Gamma's (Zotero's file kept first).

A **second attachment** therefore comes only from highlights the job left
dashed (`approx`) or lost, which stay held until a person settles them,
or from annotations Gamma may not write (another member's, refused, not
shown here). Once `new` is decided the page stays bound to the new
attachment (`_primary` keeps a bound attachment while it qualifies): the
old attachment and its
annotations stay in Zotero, the old primary's rows are dropped, Gamma's
highlights are created on the new attachment as they become eligible,
and Zotero's reader, which opens the oldest PDF (`getBestAttachments`),
keeps opening the old file.

A **park** moves no file and leaves the files as last synced (only `bind`
records the two MD5s), so a round that may send decides the pair again;
the one park recorded is that of a pair with no attachment row yet (a
page's own PDF beside a Zotero PDF it never held, a first pair whose
files stay apart): both stay.

A PDF Zotero removed or let go of while the page holds it unchanged is not
sent back (`detail.declined_md5` on the item row) until Gamma's file
changes or Zotero holds a PDF of the item again.

### The page's `zotero_pdf` mark

A page without a PDF whose item has one carries `zotero_pdf` `{key, state,
reason, size, filename}` (`_pdf_mark`, `_mark`): `remote` (in Zotero, not
asked for), `queued`, `downloading` (set when a page someone opened starts
its download) or `unavailable` (with `reason`: `linked`, `unavailable`,
`files` for a key without file access, `quota` for this server's full
storage, `failed`). `zotero_local.set_pdf` drops it when the PDF is
attached.

While something will bring that PDF, a person's own PDF is refused on the
page: `routers/pages.py` `_zotero_pdf_refusal` answers the attach
endpoints and `replace_attachment` with 409 `ZOTERO_PDF_COMING` while a
`zotero_links` row (detached included: a reattach brings the PDF) or a
whole-workspace clone (its origin's link brings it) keeps the workspace.
Otherwise the two files would part, and Zotero's annotations with them. A
mark nothing honours any more (the link removed, a page from a backup)
goes with the attach; a PDF that cannot come (`unavailable`) never
refuses one, and the copy attached then stays in Gamma.

### Downloads

With `download` `all`, every fetchable PDF is queued as a `file:` entry at
its item's version (its attachment's when that is newer), so the newest
come first. With `open`, a PDF comes
only for the `NEWEST_FILES` (50) newest items (`_wants_download`, a count
bounded by `NEWEST_FILES` on the `(parent, version)` index) or when its
page is opened: the app calls `POST /api/zotero/links/{ws}/fetch` once per
page, and `fetch_page` queues the file and the page at `OPENED_PRIORITY`
and asks for a round at once (any member who may read the page may ask;
it only reorders the link's work).

A file is **fetchable** (`zotero_merge.fetchable`) when it is stored in
Zotero Storage (`imported_*` with an `md5`) and the key has file access;
else it waits: `linked` (a file on the computer that linked it),
`unavailable` (WebDAV, file sync off, not uploaded), `no_access`. The
download itself (`zotero_remote.download_pdf`) can still find it
unavailable (Zotero's storage answers 404, the file is a zipped snapshot,
it is no PDF) or fail (a checksum, a transfer). A waiting file is shown
(`status.waiting.files`, the page's mark), never an error, and its row
waits until Zotero's version changes; a failed transfer backs off and,
after `MAX_ATTEMPTS`, waits the same way.

`unit_file` downloads (`_download`):

1. A stored PDF of the workspace with the same MD5 spares the download
   (`_stored_copy`; the row's `detail.copy` names one another page held
   when Merge Items moved the attachment here).
2. Else `download_pdf`: Zotero's `items/<key>/file` answers a 302 to a
   pre-signed storage address, which `ZoteroClient.download` fetches itself
   without Zotero's headers (the key never travels there), after
   `net_guard.validate_public_url`. The body is spooled into the
   workspace's store (`storage.Spool`), hashed as it arrives and checked
   against the attachment's MD5, this server's storage limits are asked
   every few MB, the head must read as a PDF, and the file is stored by a
   rename; nothing is left behind on any failure. This server's full
   storage (507) stops downloads for the rest of the round
   (`Round.no_downloads`) and tries the file again after
   `STORAGE_RETRY_S` (5 minutes). Its row waits meanwhile (`waiting`,
   `detail.file` `storage`) and keeps saying so when the page's unit
   decides the PDF again before the held entry runs (Zotero edited the
   item), so `status.waiting.files.storage` counts every PDF that waits
   so.
3. `zotero_local.set_pdf` makes it the page's PDF through
   `routers.pages.replace_attachment` (actor and client `zotero`, the
   relocation job owned by the link's owner, `check_held=False`: linked
   items may share a file, and `page_for_doc` then answers the oldest
   page), with the attachment row in the same transaction, under a
   compare-and-set on the page's file. When the page had a PDF with
   highlights, the relocation job places them in the new file
   ([highlight_relocation.md](highlight_relocation.md)); until it is done
   they are held.
4. In the same transaction, the item is queued again (`_after_file`), so
   the annotations that waited for the file come and Gamma's go.

### Uploads

`_upload` stores Gamma's file as the attachment's (`zotero_remote.upload_pdf`,
Zotero's three steps: authorize with the MD5, name, size and mtime, the
multipart POST to the storage address, the register): over Zotero's file
with `If-Match: <Zotero's MD5 as fetched now>`, or into an attachment with
no file yet with `If-None-Match: *`. The register's `Last-Modified-Version`
is recorded on the row, so Gamma's own upload never reads as Zotero's
change; when Zotero had the bytes already (`{"exists": 1}`) the version is
read back. The file goes under the page's original file name, else the
attachment's own, else `<doc id>.pdf`. Then the item is queued again, as
after a download.

- A 412 (Zotero's file is not the one assumed) sends the decision back to
  the item's unit.
- A 401/403 (the key may not store files after all) records a
  `zotero_rejected` row.
- **Kept copies.** When Gamma's file goes over a file of Zotero's (both
  replaced, or a first link's or force's "Keep Gamma's"), Zotero's file is
  downloaded first and kept here; while it cannot be kept (this server's
  storage full, Zotero cannot hand it out) Gamma's file waits, as a
  download would, and nothing goes over it.
- **Beside a file Zotero replaced too** (`both_new`), the new
  attachment's row names Zotero's (`detail.theirs`, read only while the
  new attachment has no file yet and dropped when the row binds), and its
  upload keeps a copy of that file the same way: it waits while this
  server's storage is full and tries a download that went wrong
  `COPY_TRIES` (3) times. For any other reason (a linked file, not in
  Zotero Storage, no file access), or after those tries, Gamma's file
  goes without a copy, since nothing goes over Zotero's, and the conflict
  is written resolved once the upload went, for the record ("…as a new
  attachment beside Zotero's, which stays there (not kept here: …)"), so
  no open conflict offers a "Take Zotero's" with nothing behind it.
- **The conflict row.** `_hold_copy` writes the open `zotero_file`
  conflict before the upload goes, its `base` naming the copy (`{doc_id,
  md5, filename, key, synced}`, `key` the attachment Gamma's file goes
  to); `upload_gc.referenced` counts an open `zotero_file` row's
  `doc_id`, so the copy is held from that moment
  ([user_db.md](user_db.md#stored-files)). An upload Zotero refuses (413,
  412, 404, 401/403) takes the row back (`_let_go`). One whose answer did
  not come asks Zotero what the attachment holds (`_went`): Gamma's file,
  and the upload counts as done, its conflict kept; another file, and the
  row is taken back and the error raised; nothing readable, and the row
  stays for the next attempt, which reuses it. A process killed after
  the upload leaves it too, so there is one row per upload. Resolved
  "keep", or once the link is removed, the copy is an ordinary file
  again, purged after 30 days; "theirs" makes it the page's PDF
  ([Resolving](#resolving)).

A **new attachment** (`_new_attachment`) is an `imported_file` under the
item, its key derived with the PDF's MD5 in the seed, its title
`UPLOAD_TITLE` ("Full Text PDF", as Zotero names a PDF it finds), its file
name the page's original file name, else its title (`pdf_filename`: at
most `FILENAME_TITLE_MAX`, 120, characters of it, `.pdf` added). Its
row is written pending, the create sent, and the upload queued once the
create lands.

A new attachment made beside Zotero's PDF keeps that PDF in its row for
as long as it is the page's PDF (`detail.beside`, `_beside`: its key, its
file's MD5 then, its row as the page last synced it, and the rows of its
annotations the page held, which drop once the new attachment is the
page's PDF). Two things read it:

- **Decided afresh.** While the new attachment is not in Zotero (its
  create not confirmed, or waiting on Zotero's full storage), the item's
  unit lets it go and decides the PDF again against the one it went
  beside, on what both sides hold now (`_beside_again`): it waits while
  the highlights are placed, goes in place once the annotations can move,
  else beside again, under the same key while Gamma's file is unchanged.
  With no row of that PDF kept (the item had no PDF when the attachment
  was decided), the create goes again under its key, but waits while the
  page's relocation runs (`waiting` / `placing`, a placing entry under its
  key, which `relocation_ended` finds).
- **Back beside.** When the page's file becomes the file of the PDF it
  went beside (its MD5 then or now: "Take Zotero's" of a `both_new`
  conflict, or a person putting the file back), that PDF is the page's
  again (`_back_beside`): its row and its annotation rows come back as the
  page last synced them, so no annotation comes here or goes to Zotero
  twice, and the plan runs against it, fetching Zotero's newer file when
  Zotero replaced it since. Gamma's attachment stays in Zotero, no page's
  PDF.

### Zotero's full storage

A 413 when an upload is authorized means the library owner's Zotero
storage is full (`_quota_full`): no PDF goes for `QUOTA_WAIT_S` (24 h),
until Sync now (`lift_quota`) or a settings change (`status.waiting.quota_until`);
an attachment Gamma made that has no file yet goes to Zotero's trash
rather than stay fileless (`detail.trashed`), and its row waits (`file:
quota`). While the wait lasts no attachment is created: the row waits
instead. When it lifts, `quota_step` queues the waiting pages and files
again, and a round that may send PDFs restores the attachments it trashed
(a receive-only round leaves them in Zotero's trash).

## Notes

### Regions

A Zotero note is HTML; a Gamma note is a block subtree. A **region** is
the part of a page kept in step with one note (`zotero_local.read_page`,
`zotero_merge.represented`):

- a **Zotero-note region**: the block a note row names (its root, marked
  `zotero_note`) and its subtree;
- the **Gamma-notes region**: the page's top-level note subtrees that are
  no Zotero-note region and no annotation, link, drawing, text box,
  picture, sheet, recording, import "Attachments" heading or chip, or note
  an RDF import brought (its `zotero_note` starting with `#`: `#item_<n>`
  from Zotero's export, `#gamma_note_…` from Gamma's;
  `zotero_local.imported_note`).
  It is sent as one child note, **"Notes from Gamma"**: the heading
  `zotero_merge.NOTES_HEADING` as its root, those subtrees as its
  children (row `slot page`, `block_id` the page id);
- a note page's region is the page itself: its title as the note's
  heading, its blocks below.

Inside a region, a block is **represented** (it is part of the note) when
its parent is, it is a note block with text, it carries none of the
annotation or attachment properties, its text holds nothing note HTML
cannot carry (a `[[reference]]`, an embed, a picture of Gamma's: code and
math spans aside), and it reads back the same through
`note_tree(note_html(…))`. Every other block is **kept**, with its
subtree: never rendered into the note, never set, deleted or moved by a
pull, only lifted out of the way. A highlight nested under a note, a
reference, a picture of Gamma's, a drawing stay where they are whatever
Zotero does to the note. The root is always represented; a root whose own
text cannot be carried is **unsettable**: a pulled change to it becomes a
`zotero_lossy` row instead of a write.

`zotero_notes.note_html` writes the represented blocks as the root's
element and its children as a list; `note_tree` reads a note's first block
element as the root and each element after it as a child, a list's items
with their nested lists as their children. The two are each other's
inverse for every tree `note_tree` returns, so a note sent and read back is
the note it was. A Zotero note's paragraphs therefore come back as list
items once Gamma sends the note.

### Pull and push

The note row's bases are `{note: hash}` of Zotero's HTML and of the
region's HTML as Gamma would send it (`note_hash`). `Blocks._note` decides:

- **Zotero changed it**: a block-level diff, never a replace
  (`zotero_merge.note_edits`). The region's represented blocks in preorder,
  `(depth, content as the note reads back)`, are aligned with the pulled
  note's with `difflib.SequenceMatcher(autojunk=False)`: an equal block
  keeps its id, content, properties and comment threads; replaced ones are
  paired in order and set (and moved when their parent changed); an added
  one is inserted under its new parent; a removed one goes after its kept
  children are lifted into its place. `zotero_local.note_ops` turns the
  edits into ops under the write lock, so positions see the page as it is.
- **Gamma changed it**: a PATCH of the note's HTML. The "Notes from Gamma"
  note goes only once the page has been still for `NOTES_QUIET_S` (60 s;
  the page root's `updated_at` moves with every write on the page), the
  page's entry queued with that time (reason `NOTES_WAIT`): typing and
  indenting never create or trash Zotero notes. A note over `NOTE_MAX`
  (500,000 characters) is refused (`zotero_rejected`).
- **Both changed it**: Gamma's goes, and a `zotero_note` row keeps
  Zotero's note for "Take Zotero's". A lossy note writes neither side:
  a `zotero_lossy` row offers both.
- **Zotero trashed it** while the region is unchanged here: the represented
  blocks go, the kept ones lifted out first (`note_removal`), nothing is
  sent. Changed here (or back here, or restored from a backup): the note
  comes back in Zotero, with the region's text unless Zotero's already
  reads as the region does.
- **Gone here** (every represented block deleted): trashed in Zotero,
  unless Zotero changed it since (the region comes back,
  `zotero_deleted_edited`). A note is trashed only when every block it
  represents is gone: the row keeps their ids (`detail.rep`), and while
  any of them is still on the page (moved into another note) the note
  stays in Zotero.
- **Its first line deleted here** (Gamma's editor lifts the lines under a
  deleted block into its place): the region is rooted again on the first
  of its remaining blocks, marked as the note's (`_reroot`), and decided
  by the next unit, so what is left reads as Gamma's edit of the note.

A Zotero note with no region yet comes in as a new region at the page's
end, its root marked `zotero_note`; a note whose first line is the
"Notes from Gamma" heading stands for the page's own notes, its children
landing at the page's top level (one per page). The page's own notes go
out as a new "Notes from Gamma" (`_create_gamma_notes`, its row pending
first) when the page has such notes, no note of the page stands for them
and the page is still.

### Lossy notes

A Zotero note is **lossy** (`zotero_merge.note_lossy`) when reading it into
blocks and writing it out again (`note_html(note_tree(html))`) would hold
fewer of some mark (a citation, an annotation embed, a subscript, a table,
a colour, a style; each counted in one spelling, `<b>` and `<strong>`
alike) or other text. Paragraphs becoming a list do not count. A lossy
region's root carries `zotero_readonly: "lossy"`, and Gamma never writes
that note back, whatever the path (push, first link, force, resolve),
except through a `zotero_lossy` row's "Send to Zotero anyway"
(`detail.force`, spent once it went). Gamma's edits to it stay in Gamma,
in one open `zotero_lossy` row per block refreshed in place. A pulled
change Gamma cannot set on a block (an unsettable root) is a `zotero_lossy`
row too, whose "Take Zotero's" sets that block.

### Pictures

A picture in a Zotero note is an `embedded_image` attachment under the
note (`<img data-attachment-key="K">`). Pulled, it is downloaded
(`zotero_remote.download_image`: a PNG, JPEG, GIF or WebP of at most 25
MB), stored as an upload, and recorded as an `image` row (`detail.name`);
the block shows it as `![alt|width](/api/uploads/<name>)`. While notes are
compared, such a picture stands as a placeholder holding its key
(`zotero_local.resolve_pictures`), and Gamma sends it back as the same
`<img data-attachment-key>` tag (`picture_tags`), so it round-trips. A
picture Zotero cannot hand out stays Zotero's (`(image)` in Gamma) and
makes the note lossy. Pictures Gamma's own notes hold are kept blocks and
never go. When a first link gives a note's pictures their rows during the
unit, the note is compared by the next unit, as it reads with them.

## Annotations

### Which blocks

An **annotation block** (`zotero_annotations.is_annotation_block`) is a
highlight that is no link region, an ink group on a PDF page, or a text
box on a PDF page; a sheet among its ancestors makes an ink group or a
text box none. It may be created or written in Zotero
(`eligible`) when the page's PDF is the attachment's file (MD5) and
nothing holds it (`hold_reason`):

| Reason (`detail.reason`) | |
|---|---|
| `still drawn in the PDF` | its original is still embedded in the file (`still_embedded`): Zotero would show both |
| `being placed in the new PDF` | a highlight measured on another file (`pdf_position.doc_id`: its relocation is not done), or an ink group whose file is not stored yet |
| `dashed in Gamma until accepted` | a rough match after a PDF replacement (`approx`), pending review |
| `not found in the new PDF` | `lost` after a replacement |
| `not placed on the PDF` | a highlight with no frame or no placed rect; a block whose place cannot be read now (its PDF page, its ink file); a block with a row that is no annotation block now (a text box moved onto a sheet) |
| `the PDF is not here yet` | the page has no PDF (`WAIT_FILE`) |
| `the PDFs differ` | the page's PDF is not the attachment's file (`WAIT_DIFFER`) |

A block with a row that has a reason is **held**: nothing is pushed,
nothing trashed for it, and Zotero's changes wait in the row's `zbase`
until it may be written again; then the merge takes them. Only a block that
no longer exists, or now lives on another page, is gone. A Zotero
annotation Gamma cannot show yet gets a `waiting` row and comes once the
file is the same. One Zotero trashes while it waits is `trashed`, no
longer counted as waiting; restored in Zotero, it waits again while the
files differ and comes with the PDF. The reasons are counted in
`status.waiting.annotations.reasons`.

### Kinds and positions

| Zotero | Gamma |
|---|---|
| `highlight`, `underline` | a highlight (an underline stays one: the row's type is sent back) |
| `image` | an area highlight (`pdf_position.area`) |
| `ink` | an ink group; one item per look |
| `text` | a Zotero box ([Text boxes](#text-boxes)), its rect the box, upright on the annotation's centre at its font size |
| `note` | a text box in note yellow at the icon's corner, as wide as its text |

The type never changes (Zotero refuses it). Positions convert through the
page's frame, its view box and rotation (`viewer_rect_to_pdf`,
`to_viewer_rect`, pdf_export's mappers), never a plain media-box flip, so
cropped, offset and turned pages land where they are drawn. Positions go
out at three decimals, as the reader writes them, and compare at hundredths
of a point. What Gamma cannot hold stays Zotero's by the two bases: a
turned text annotation stands upright here, a passage's `nextPageRects`
stay in Zotero, a text box's fill stays Gamma's.

- **Quote** (`annotationText`, highlight and underline only): as Zotero
  sent it, up to 7,500 characters, never collapsed or clipped on the way
  in; read from the glyphs under the rects when Zotero sent none. Sent
  when the quote itself changed.
- **Page label**: the PDF's `/PageLabels` as pdf.js reads them
  (`pdf_text.read_page_labels`: the number tree breadth first, a node with
  `/Kids` read for its kids alone; the styles D, R, r, A and a, letters
  repeating, 27 as AA; `/St`; a prefix alone without a style or with the
  empty name; the prefix a text string, UTF-16 or UTF-8 after its byte
  order mark, else PDFDocEncoding; a tree pdf.js refuses gives no labels),
  else the page number, as the reader's `_getPageLabel` labels an
  annotation (`zotero_annotations.item_label`), at most 50 characters.
  Sent on a create, and with a position only when the page changed from
  the base, so a label corrected in Zotero stays. A row whose label is not
  settled (`detail.labels`) is checked once, as its annotation is decided
  (`Blocks._label_due`): when Gamma made the annotation (its key the one
  its create derives, `_made_here`), Zotero still holds the label
  PyMuPDF's `page.get_label` gives the page, in MuPDF's syntax and its
  prefix undecoded (`<FEFF0031>` for hyperref's "1"), and pdf.js reads
  another, that one goes, as a PATCH of the label alone when nothing else
  changed; any other label stays, and an annotation brought from Zotero is
  settled as it comes. `blocks_dirty` counts such a row only on a page
  whose two readings differ (the PDF's labels read for that alone), and
  the listing of Gamma's changes queues each page with such a row once
  (`label_checks`, [Listing Gamma's changes](#listing-gammas-changes)).
- **Sort index** (`sort_index`, the reader's `getSortIndex`):
  `PPPPP|OOOOOO|TTTTT`, the page index; the index of the character nearest
  the sort rect by the reader's `rectsDist`, among the page's characters
  without whitespace or boxless entries (a ligature MuPDF spells out kept
  as one, as pdf.js keeps it); and `floor(max(0, view height − rect[3]))`.
  The sort rect is the rect with the greatest `r[2]`, its x2 (the first
  of equals): the rect the reader's own sort puts first, which the
  reader's code calls top-most, not the one with the greatest y2 (the
  research note quotes the reader's rule). For ink it is the paths'
  bounding box. MuPDF's
  characters stand in for pdf.js's, so the offset can miss by a few
  characters; the sidebar's order is the same. Sent with every position.
- **Colour**: `#rrggbb` lowercase going out, the alpha dropped
  (`color_out`); coming in, the block keeps its own alpha (a new block
  takes 0.45, `HIGHLIGHT_ALPHA`). A sticky note's colour is never compared
  or sent (its projection's colour is `""`): Gamma's text box has no place
  for it, so a new one goes as Zotero's yellow (`#ffd400`) and Zotero's
  colour stays.
- **Comment**: for highlight, underline, image, ink and note, the block's
  own note and the notes nested under it, written as the RDF export's
  popup text (`comment_out`: the nested notes as an indented list, bold
  and italic as the reader's `<b>` and `<i>`). A pulled comment whose
  nested part is unchanged updates the block's own note alone
  (`comment_parts`, `comment_in`); a change in the nested part is no change
  Gamma can apply to one block, so it is a `zotero_annotation` conflict and
  Gamma's goes. A `text` annotation's comment is the box's plain text both
  ways, its blank lines kept (`plain_text(…, gaps=True)` going out,
  `markdown_of` coming in). A drawing's later items and a split piece
  carry no comment.

### Text boxes

A `text` annotation and its block's text box are one rectangle. The box
is a Zotero box (`text_box.zotero`,
[text_boxes.md](text_boxes.md#zotero-boxes)): it has no padding and
draws in the face of Zotero's reader, so its `x`, `y`, `w`, `h` are
Zotero's rect and both apps wrap its text at that width. The mapping is
the identity both ways (`_text_box_from`, `_text_box_fields`): a rect goes
to a box and back to hundredths of a point. A `note` annotation's box is
a Gamma box in note yellow and never takes the key.

- **From Zotero**: a new block's box is a fixed Zotero box at the rect, in
  the annotation's colour. A pull keeps what Zotero has no place for, the
  box's fill and its width mode, with one rule for the width.
- **Width mode** (`apply_fields`). Zotero has no auto width: its rect is
  the width it wraps at. A pull that changes an auto box's width by more
  than `POSITION_TOL` (0.5 pt), its text or its font size makes the box
  fixed at Zotero's width, and so does a text merged from both sides
  (`_merge_annotation`). A pull that only moves it keeps `auto`. Nothing
  goes back for it.
- **To Zotero**: an auto box goes at the width it stores, which the client
  measured plus `ZOTERO_SLACK` (1 pt), as Zotero's own `measureWidth`
  sizes one, and its `h` as the client measured it, at the screen's line
  height.
- **A Gamma box's first send** (`Blocks._zotero_box`, called from
  `_items`). A text box without the key is made a Zotero box once per
  unit: the change is planned on the block (`_change`), so it is an op of
  the unit's transaction, and the items are made from the converted
  block. The row's signature is the converted block's (`Blocks._sig`, for
  a create, a resend, a restore and a merge), so the next round finds it
  as synced. A box no `text` row links becomes its text area (`_framed`:
  `text_box.zotero_frame` on the page's size, a line, `size ×
  ZOTERO_LINE`, taller for each blank line of its text, since Zotero's
  textarea gives a blank line a line and Gamma's box none). Its text stays
  where it was drawn; it loses its padding and takes Zotero's face.
- **A linked box without the key** (`_needs_key`): one stored without it,
  or written back by a writer that drops it (an older client or clone, a
  writer that names no base box). It gains the key alone, its geometry as
  it is, with no Zotero request, since its rect is the box already.
  `blocks_dirty` counts such a live or read-only row, and the as-synced
  skip lets it through to `_merge_annotation`. Its row's Gamma base is
  read with the block's blank lines (`_keyed_base`: a box without the key
  went to Zotero with them dropped), so the key alone reads as no change
  on either side, and a trash in Zotero finds the box unchanged here. A
  page so stored and not changed since comes once through `box_checks`
  ([Listing Gamma's changes](#listing-gammas-changes)); a box that loses
  the key later is an edit of its page, which the feed brings. A
  receive-only round lists no Gamma changes, so there the key comes with
  Zotero's next change to the item, or once the link is two-way again.
- A box stored without the key whose text has a blank line Zotero lacks
  keeps Zotero's text as it was until the box's next edit here.

### Ink

An `ink` item has one colour and one width, so an ink group is one item per
**look** (tool, colour, size and opacity, `ink_look`). Each ink row owns
the ids of its strokes (`detail.stroke_ids`). `to_items` assigns the
block's strokes to its rows by id first (`_ink_groups`): a row keeps its
key while its strokes change look, which is a PATCH of that item's colour
or width; a stroke no row owns joins the first row of its look that is not
another member's, else a new slot. A pull replaces exactly the strokes its
row owns (`replace_strokes`, new ids `z<key>-<n>`) and restyles them for a
new colour or width; Zotero's strokes are taken as they are, never merged
with the stored drawing. An item's width is its strokes' mean drawn width;
pressure, timing and grain stay Gamma's. A position past `POSITION_MAX`
(65,000 bytes, the client's limit under the server's 65,535) is split into
pieces, each its own item (`slot` `<slot>.<n>`), as Zotero's client splits
one.

### Creates, changes and removals

- **New in Zotero**: a block after the page's last annotation block
  (`annotation_inserts`), its ink file stored first, marked
  `zotero_annotation`, `imported_annot` and `annot_stripped`.
- **New in Gamma** (two-way, same file, eligible): each item of the block
  no row stands for is created under a key derived from its block and slot
  (`_create_annotations`), its row pending first.
- **Changed**: field by field against the two bases (`_merge_annotation`);
  Zotero's fields applied with `apply_fields`, which touches only those
  fields and drops a highlight's relocation marks on a new position as a
  move by hand does; Gamma's as a PATCH of those fields
  (`patch_fields`: the sort index with a position, the page label only
  when the page changed).
- **Deleted here**: trashed in Zotero, unless Zotero changed it since (an
  edit beats a delete: the block comes back), or it is another member's
  (put back at once, with a `zotero_locked` row).
- **Trashed in Zotero**: the block goes, its children lifted to its place
  (for one item of a drawing, that item's strokes alone), nothing sent;
  unless it was changed here, came back here or was restored from a
  backup, when it comes back in Zotero.
- **Removed for good** (`zotero_sync_blocks.children_gone`): a block or
  region unchanged since it last synced goes, what Gamma keeps under it
  lifted out; one changed here stays and goes to Zotero again under a
  fresh key. The write's compare-and-set derives again what each doomed
  block was decided on (its signature, a region's hash and blocks), so
  typing elsewhere on the page does not stop the removal; an edit to a
  doomed block decides it again on the page as it is, at most
  `GONE_READS` (3) times, after which nothing goes.
- **Moved in Zotero** (Merge Items): an annotation now under the PDF
  attachment of another synced page moves to that page with its block and
  subtree ([Items and pages](#items-and-pages)).
- **A new primary**: the old primary's rows are dropped (their annotations
  stay in Zotero with it) and the page's blocks are new to the new one.

### Group libraries

In a group library Zotero lets only an annotation's maker change it, and
the maker is the user whose key wrote it (`meta.createdByUser`), never
what the JSON says. An annotation another member made (`_author`) is
**locked**: its row is
`readonly`, its block carries `zotero_readonly: "locked"` and
`zotero_author`. It is pulled as any other and never written: an edit
here stays here with a `zotero_locked` row, a delete here is put back in
the same round, and it is never created anew under this key, nor carried
to a new primary. Strokes the person adds to another member's drawing are
their own and go as a new item. Everything Gamma writes with one key is
that key's user's in Zotero's eyes.

## Writing to Gamma

### Actor and client

Every write the link makes in Gamma is logged as actor `auth.ZOTERO_ACTOR`
(`"zotero"`, shown as "Zotero" by `auth.actor_names`) and client
`auth.ZOTERO_CLIENT` (`"zotero"`). The link's clock skips its own client
only: a clone's pushes (client `sync`) into a linked workspace are local
edits for this engine, since a linked workspace may be another server's
clone origin. `inbox.from_ops` skips client or actor `zotero`, so text typed
in Zotero raises no mention or notification here.

### Transactions and the compare-and-set

A unit's Gamma side is one transaction (`zotero_local.apply(ws, page_id,
ops, check=, rows=, trees=)`), under the workspace's write lock:

1. `check(conn)` reads again what the decision was made on; False raises
   `Changed` and nothing is written;
2. the label or folder inserts the ops file into (`trees`), then the page's
   ops, in `MAX_OPS` chunks; `ops` may be a function of the connection, so
   positions are computed under the lock on the page as it is;
3. `_guard` refuses an op that names a block of another page (`Moved`,
   an `OpError` 409): for the engine the row's block is then gone from its
   page, and the block is new where it is now;
4. `ops.apply_batches` with the link's actor and client;
5. `rows(conn)`: the engine's `zotero_objects` rows (and queue entries
   that must follow the write, such as an item queued again after its PDF
   arrived), so a process killed between the write and its bookkeeping
   loses nothing;
6. the commit, then `ops.after_commits` inside the connection's block (the
   room's fan-out, the commit listeners).

`create_page` makes a page from Zotero the same way: `check`, the label
inserts, `blocks_store.insert_page_tree` (a node's id is kept, so the rows
can name its blocks), `upload_gc.claim` of the files it names, `rows`, the
commit, the listeners. The writers that commit on their own take the same
`check` and `rows` and run them inside the transaction they commit:
`trash_page`, `restore_page` and `delete_folder` through
`_own_transaction`, `move_block_across` in its own, `set_pdf` through
`replace_attachment`'s. The first four are no-ops on a page, folder or
block already where it is going, so a unit run again is no error.

The **compare-and-set** reads `zotero_local.stamps(conn, ids)`: `{block id:
(page id, updated_at)}`. Every set, move and cross-page move restamps a
block, and every write on a page restamps its root, so an item's unit
checks the page root's stamp as it read it; the collections pass checks
every folder's; a download checks that the page's file is the one it
decided on; a removal for good derives its plan again and compares. A
failed check writes nothing on either side: the unit's queued Zotero
writes are never sent, its entry goes back to the queue, and a round comes
for it a few seconds later ([The round](#the-round)).

### `replace_attachment`

`routers/pages.py` `replace_attachment(ws, page_id, doc_id, …, actor,
client, owner, check_held, props, check, rows, check_zotero)` makes a file
a page's PDF in one transaction and then starts the relocation job that
places the page's highlights in it. `PUT /pages/{id}/attachment`,
`zotero_local.set_pdf` (a download) and the `zotero_file` resolver all go
through it. The link passes `check_held=False` (linked items may share one
PDF), `check_zotero=False` (the Zotero refusal is for a person's attach),
`props={"zotero_pdf": None}` and its own `check` and `rows`; the job is
owned by the link's owner. A job that does not start (the account's jobs
are full) leaves the highlights where they were, held, with the reason in
the answer's `relocation_error`.

A conflict resolved "Take Zotero's" is written as actor `zotero` with
client `""`, as a person's edit: the next round sends it back and both
sides hold Zotero's. A lossy note's "Take Zotero's" uses the link's client
and renews the bases instead, since Gamma must not send that note.

## Writing to Zotero

A unit gathers its Zotero writes in one `zotero_remote.Writes` and sends
them with `flush` after its Gamma transaction committed:

- **Order**: `STAGES`, `collections` (a parent before its subcollections),
  `items`, `attachments`, `notes`, `annotations`, then the trash flags; an
  object whose parent (`parentItem`, `parentCollection`, a collection it is
  filed in) went out in the same `Writes` and did not land is not sent
  (`transient`).
- **Batches**: 50 objects a request (`WRITE_LIMIT`), Zotero's
  multi-object POST, whose update has PATCH semantics (a field left out
  stays as Zotero has it). Each object carries its own `version` (0 to
  create it under the key it names), never the library's
  `If-Unmodified-Since-Version`: one stale object then fails alone instead
  of the whole batch. One key written twice before a flush is one write. A
  request refused whole (400, 413, 500) for one object in it is sent again
  in halves, to find it. A retype is a PUT of one item
  (`ZoteroClient.put_item`, `If-Unmodified-Since-Version` its version).
- **Trash**: `deleted: true` in a write, `deleted: false` to restore.
  Gamma never sends a DELETE, so Zotero's own users can still bring back
  whatever Gamma trashed.
- **Stops**: no answer, a refused key, a gone library or a busy Zotero
  (429, 502, 503, 504) stops the flush; the rest is `transient`, and the
  failure stops the round once the unit is recorded (`_Unit.stopped`).

Each object's `Outcome` is recorded on its row (`_Unit._record`,
`Blocks.record`, `_Folders.record`):

| Outcome | Zotero answered | What the row does |
|---|---|---|
| `ok` | saved (`successful[i]`, the object as Zotero holds it now) | `version` and `zbase` from the saved object, `gbase` and `gsig` as decided |
| `unchanged` | Zotero held that already | the same, from the decision |
| `conflict` | 412 on an update, or a 404 (it went meanwhile) | nothing; the unit is queued again and decided again on a fresh read |
| `exists` | 412 on a version-0 create; the object is read back | ours or not, by kind and parent ([Keys and identity](#keys-and-identity)) |
| `transient` | no answer, 408, 428, 429, 5xx, a parent not saved | the page backed off and tried again later |
| `rejected` | any other 4xx, with Zotero's sentence | a create's row `rejected`; any row `detail.rejected` (the signature refused) and `reason`; a `zotero_rejected` conflict. Sent again only once the signature changes |

Every write outside the multi-object POST records the version it answered:
a file's register, a PUT's `Last-Modified-Version`.

**The client** (`zotero_web.ZoteroClient`) sends `Zotero-API-Version: 3`
and the key in `Zotero-API-Key`. Every client of one key shares a
throttle: at most `KEY_CONCURRENCY` (4) calls in flight and the pause
Zotero asks for with `Backoff` or `Retry-After` (each capped at 60 s),
which every call of the key waits. A 429 or 503 is tried again (four times
at most), and so is a GET that got no answer or a 500, 502 or 504 (a
download only until a byte of it arrived). The client calls its
`heartbeat` every second while it waits and while a file streams, so a
detach stops a round at once; errors are `ZoteroError` subclasses whose
text never holds the key. Reads page 100 objects at a time; reads by key
name 50 keys a request, several requests in flight (`objects`). An API
call may stall `TIMEOUT` (30 s), a storage transfer `FILE_TIMEOUT` (120 s)
between two reads.

## Conflicts and the log

### Conflict kinds

A link's conflict rows are `sync_engine`'s `sync_conflicts` rows, of the
kinds `zotero_sync_rows.CONFLICT_KINDS` (a literal tuple: the frontend's
test reads it). Sync never blocks on one: Gamma's side has already won
(or, for a first link and a force, the adopted side), and the row keeps
the other for the person to take instead.

| Kind | When | Choices |
|---|---|---|
| `zotero_field` | record fields (or a standalone PDF's title, or a folder's name or place) changed on both sides; one row per page per unit, listing every field (a folder's: one row per field) | keep; "theirs": Zotero's values into the page's record, the standalone PDF's title or the folder |
| `zotero_note` | a note changed on both sides | keep; "theirs": Zotero's note into its region, as a pull writes it |
| `zotero_annotation` | an annotation field changed on both sides, or Zotero changed its comment's nested part | keep; "theirs": Zotero's values into the block |
| `zotero_file` | the PDF replaced on both sides (Gamma's went over Zotero's, or beside it as a new attachment), or Gamma's went over Zotero's at a first link or force | keep; "theirs": Zotero's kept copy as the page's PDF (its highlights relocate onto it) |
| `zotero_lossy` | a lossy note changed here, or a pulled block Gamma may not set | keep; "mine": send Gamma's anyway at the next round; "theirs": Zotero's into the region, or into the block |
| `zotero_deleted_edited` | an edit beat a delete, either way | keep |
| `zotero_locked` | another member's annotation changed or deleted here | keep |
| `zotero_rejected` | Zotero refused a write, or a unit was given up after its tries | keep |

`record_conflict` writes a row after the commits it is about, never inside
a unit's transaction. The kinds Gamma never sends (`ONE_OPEN`:
`zotero_lossy`, `zotero_locked`, `zotero_rejected`) keep one open row per
block, refreshed in place, so each burst of edits adds none. `mine` and
`theirs` are readable lines (`zotero_merge.field_line`: "Year: 2017",
"Authors: A. Vaswani, N. Shazeer, …"; `annotation_line`: "yellow · p. 3 ·
“the quote” · the comment"; a note's text). `base` keeps what the
resolver needs and Zotero's side as the row held it once the unit was done
(`zbase`):

- `zotero_field`: `{theirs: {field: value}, zbase: {field: value}}`
  (`field_base`);
- `zotero_note`: `{key, note: Zotero's HTML, zbase}`; `zotero_annotation`:
  `{key, z: Zotero's projection, field, zbase}` (`_synced_side` refreshes
  `zbase` once the unit's writes land);
- `zotero_lossy`: `{key, note}`, or `{key, block, content}` for one block;
- `zotero_file`: `{doc_id, md5, filename, key, synced}`, the kept copy and
  Gamma's MD5 that went over it.

`result` says what was done: "The note changed on both sides: Gamma's went
to Zotero"; on a receive-only round, "…: Gamma's is kept here; it goes to
Zotero once the link sends changes again" (`KEPT_HERE`, which the card
reads by its end); for a first link's pair that took Zotero's, "The note
differed from Zotero's: Zotero's was taken here". Differences a first link
or a force decided are written **resolved**, kept for the record, with a
summary line in the log for each round that decided some; the Conflicts
lists show open rows only (`list_conflicts` defaults to them), and
`GET …/conflicts?resolved=1` lists the resolved ones.

### Resolving

`zotero_sync.resolve_conflict(ws, id, choice)`: `keep` marks the row
resolved and writes nothing; `mine` and `theirs` first write that side
through the kind's resolver (`RESOLVERS`), then mark it. A kind with no
resolver, a choice it does not offer, a row resolved already, or a write
the block refuses answers 409 and leaves the row open.

- `resolve_field` (`zotero_sync_resolve.py`): a paper's values through
  `record_op`, a standalone PDF's title as the page's title, a folder's
  name and place (`_resolve_folder`, under the folder of the parent
  collection Zotero names, the top when it has none here).
- `resolve_file`: the kept copy through `replace_attachment`; refused when
  the copy is gone or the attachment's file changed again since (the
  attachment row's `md5` is no longer the row's `base.synced`: "the PDF
  changed again since: this conflict can only be kept"). For a PDF
  replaced on both sides that went beside Zotero's, `base.key` is Gamma's
  new attachment, so Zotero replacing its own file again refuses nothing:
  the page's file is then the PDF the new attachment went beside, whose
  attachment the next item unit makes the page's PDF again, fetching
  Zotero's newer file ([Uploads](#uploads), "Back beside").
- `resolve_note`, `resolve_annotation`, `resolve_lossy`
  (`zotero_sync_blocks.py`): Zotero's note into the region as a block-level
  diff, its pictures by their rows or downloaded (`_note_into_region`);
  Zotero's annotation values through `apply_fields`; a lossy note's
  "mine" sets `detail.force` on the row, queues the page and asks the
  link's clock for a round at once.

**Zotero moved on.** "Take Zotero's" over a value Zotero has changed again
since the row was written would write the old value back over Zotero's
newer one, which came here meanwhile. The resolvers compare the row's
`base.zbase` with the object row's `zbase` now (`_moved_on`,
`_refuse_moved_on`) and refuse with 409 `MOVED_ON` ("Zotero changed this
again since: its newer value is here now"); the row stays open, to keep. A
row from before `zbase` was kept is never refused.

### The log and the notice

Log lines go through `sync_engine._note` into `sync_log`, in the mirror's
words: `pulled`, `pushed`, `created here`, `created there`, `deleted here`,
`deleted there`, `restored here`, `restored there`, `replaced here`,
`replaced there`, and `kept here` (a folder left here while its collection
is in Zotero's trash). A page made or brought back counts one added, a
page deleted one removed, anything else one changed
(`zotero_sync_rows._ACTION_STATS`). A first link's or a force's decisions
add a line for each round that made some, "replaced here: 212 records, 31
notes" (or "replaced there" for Gamma's side).

Open conflicts raise the notice `zotero-conflicts` (`notices.py`
`zotero_conflicts`, pane `workspaces`: "{n} sync conflicts to look at in
your Zotero link"; [settings.md](settings.md#notices-the-red-dot)).

## First link and force

### First links

`zotero_state.first` marks a first link in progress until it is spent
([Finishing](#finishing)):

- `new`: a link made with a new workspace (`create_link` without
  `workspace_id`). The first fill is an ordinary listing from version 0;
  the pill reads "First sync 300 / 2,000" while it lasts.
- `existing`: a link of an existing personal workspace, the dialog's
  "Into". Its pages and the library's items are paired before anything is
  listed, and each pair's differences are decided by `status.adopt`
  (`theirs`, Zotero's version, or `mine`).
- `notes`: notes access granted to a key that had none (below).

**Matching** (`zotero_sync_first.first_match`, while `version` is 0): the
library's top-level items and attachments are read whole, page after page
in the order they were added, the trash with them (`WHOLE_READ`:
`includeTrashed=1`, `sort=dateAdded`, `direction=asc`, as
`ZoteroClient.children` reads), so an item edited or trashed while the
pages are read keeps its place and is neither skipped nor read twice;
what is in the trash takes no part. Then
`match_inputs` lists the workspace's pages no row holds (each with its
stored PDF's MD5) and the items no row holds (each with its PDFs' MD5s; a
standalone PDF with its own). `zotero_merge.match` pairs them one to one,
greedily by strength: the page's `zotero_key` naming this library and the
item; the DOI; the arXiv id without its version; the page's PDF MD5 among
the item's; then title and year, only when neither side has a DOI or an
arXiv id and the two do not both hold PDFs that differ. Each page and item
is used once; ties go to the oldest page, then the oldest item. Note pages
pair with standalone notes by the key they name (`_note_pairs`). Every
pair's rows are written in one transaction before anything is reconciled,
marked `detail.first` (and `matched`, the reason); a pair whose page holds
one of the item's PDFs has that attachment bound too. Only pages and items
without rows take part, so a match run again after an interruption pairs
nothing twice.

The rest pairs as each part is reached:

- folders with collections by their paths of names, ignoring case
  (`_Folders._matches`, `match_paths`);
- annotation blocks with Zotero's annotations by `zotero_annotation`, then
  by `imported_annot`, the key the embedded import gives the annotation
  Zotero would embed for it (still-embedded blocks included: a matched one
  gets a row, so Zotero's annotation does not come in as a second block
  beside it);
- note regions with child notes (`Blocks._match_note`): a block marked with
  the note's key; else a top-level note block holding the note's words;
  else a block an RDF import marked (`#item_<n>`) whose words are at least
  `NOTE_ALIKE` (0.8) alike, Zotero having edited it since the import;
- a note whose words equal the region's agrees whatever shape its HTML
  takes (`note_words`).

**Deciding a pair.** `zotero_merge.first_link_base` gives each pair bases
holding only what both sides agree on: a field both hold alike keeps each
side's value, so neither reads as changed; a field they hold apart is None
in both, so both read as changed and `adopt` decides it; a value only one
side holds is that side's addition and goes to the other whatever `adopt`
says (a collection's top-level parent excepted: `""` is a place there); a
set's base holds the members both have, so nothing is removed. A
position agrees within `POSITION_TOL` (0.5 pt). What `adopt` decided is
written as resolved conflict rows naming the side taken. On top of the
fields:

- **notes**: by `adopt`, except a lossy Zotero note, which keeps Zotero's
  whatever `adopt` says;
- **trash**: the adopted side's trash goes to the other side's. Adopting
  Zotero's, an item in Zotero's trash sends its page to Recently deleted,
  and notes and annotations in Zotero's trash are removed here (Gamma's
  differing words kept in resolved rows). Adopting Gamma's, a page in
  Recently deleted sends its item to Zotero's trash, what Zotero trashed
  comes back there (a note's body sent only where the words differ), and
  what Gamma deleted goes to Zotero's trash;
- **PDFs**: the same MD5 binds them. Different files: Zotero's becomes the
  page's PDF (Gamma's highlights relocate onto it) whenever Zotero's
  attachment holds annotations Gamma cannot move (`_unmovable`,
  [The plan](#the-plan)), or `adopt` is Zotero's and the file is
  fetchable; Gamma's goes over Zotero's in place only when `adopt` is
  Gamma's, every annotation under Zotero's can move and uploads may go
  (Zotero's file kept here first, in an open `zotero_file` row), and
  while the page's highlights are being placed it waits; a file Zotero
  Storage does not serve is never written over, and otherwise both stay
  (the annotations wait: "the PDFs differ").

`status.adopt` stays until the first link is spent
([Finishing](#finishing)); a pair still to decide then keeps its side as
`detail.adopt`.

### The dialog's preview

`zotero_sync.preview(owner, library, workspace_id)` (`GET
/api/zotero/links/preview`) counts what linking would move: the library's
top-level items, notes and attachments (`Total-Results`), and among the
first `PREVIEW_PAGES` (20) pages of attachments, the PDFs in Zotero
Storage, their bytes and the linked PDFs Gamma cannot download (`partial`
when there are more). Into an existing workspace it runs the first link's
own match (`preview_first_link`, reading a page's PDF only when a Zotero
PDF has its size): `matches`, `to_send` and `to_send_bytes` (papers that go
as new items), and what the first rounds add in Zotero whatever `adopt`
says: `collections_to_add`, `tags_to_add`, `notes_to_send`,
`highlights_to_send` (on paired pages holding their item's PDF) and
`highlights_new` (on papers whose PDF the link would upload; they go only
once it does). Past 2,000 items, attachments or collections those are
None: the first round pairs everything all the same. It is bounded: one
count per account at a time (429), at most `PREVIEW_DEADLINE_S` (30 s)
waiting on Zotero (503), 30 counts per 10 minutes per account, and the same
count asked again within `PREVIEW_CACHE_S` (120 s), neither the library's
version nor the workspace's change number moved, is answered from the last.

### Force

`force_sync(ws, "pull" | "push")` notes `status.force` and runs a round in
the background. A detached link cannot force, and a receive-only link (or
one whose key cannot write) cannot force push: `force_sync` refuses it,
and a round that finds itself receive only drops a push asked for before
with an error. `_start_force` turns it into the round's policy: every row
(but pending ones and pictures) is marked `detail.first` and loses the
side an earlier pair kept (`detail.adopt`), `version` and the local cursor
start over, so both sides are listed whole, and `adopt` is the forced side
(`theirs` for pull, `mine` for push); `status.forced` says a force is
under way until it is spent ([Finishing](#finishing)). `queue_changes`
lists a row marked `first` even at its version, so every pair is decided
again, receive only included. A force then decides each pair as a first
link does, with the forced side winning, and takes that side's trash
across:

- a **pull** sends to Recently deleted the pages whose items are in
  Zotero's trash (kept there 30 days, as any page), deletes from their
  pages the notes and annotations Zotero trashed, edited here or not, with
  no copy in Recently deleted (their words that differ go to resolved
  rows; Zotero keeps them in its trash, and restoring one there brings it
  back), and brings back the pages whose items Zotero holds;
- a **push** sends to Zotero's trash the items whose pages are in Recently
  deleted or were purged, and the notes and annotations Gamma deleted, and
  brings back out of Zotero's trash what Gamma holds. It still takes
  Zotero's version of a lossy note, unless Send anyway was chosen for it
  ([Lossy notes](#lossy-notes)), and of a PDF whose Zotero annotations
  Gamma cannot move: Zotero's file comes over the page's, as at a first
  link ([The plan](#the-plan)).

A PDF a push may not send (uploads off, a file Zotero Storage does not
serve) or a pull cannot fetch (no file access) stays as it is on both
sides. Gamma never removes anything from Zotero for good: what a force
deletes there waits in Zotero's trash.

What a force replaced is kept only in resolved conflict rows, which the
Conflicts lists leave out (`GET …/conflicts?resolved=1` lists them), and
counted in a log line for each round that replaced some. The one replaced
thing kept open is Zotero's PDF that a push writes Gamma's over, in a
`zotero_file` conflict ([Uploads](#uploads)). A page's PDF replaced here
is kept in no row: once nothing names it, the uploads clean-up purges it
30 days later ([user_db.md](user_db.md#stored-files)). Gamma keeps no
history of a page, and a force's writes are no edit the page's undo takes
back, so only a workspace backup taken before the force holds a page as
it was; the confirmations say so ([Frontend](#frontend)).

### Notes access granted later

A key without notes access sees no note: the link moves none either way,
so the notes Zotero holds and the notes Gamma has are strangers. When a
round finds notes access newly granted (`_access`), it sets
`zotero_state.version` to 0, the local cursor to `""` and, when no first
link is in progress, `first` to `notes`: the library is listed whole
again, so the notes come, and every
page is looked at, so Gamma's go. Before that listing, `first_notes` pairs
Zotero's standalone notes (read whole as a first link reads,
`WHOLE_READ`, so a note edited during the read is neither skipped nor
read twice) with the note pages that name them (a link's before it was
made again, an import's), and while `first == "notes"`
`Blocks._match_note` pairs child notes with regions as a first link does;
each pair takes Zotero's side (`adopt` is `theirs`), so no note is made a
second time on either side.

`relink_link(ws)` takes no side: a reattach keeps the status's `adopt`,
so a first link detached before it finished keeps its own side. A backup
restored while the link was detached is handled by the next round
(`_own_library`, [Backup restore](#backup-restore)): another library's
rows are wiped and the workspace matched again, the restored pages
winning (`adopt` `mine`).

## Backup restore

A workspace backup holds the link's pages.db rows with the pages
([workspaces.md](workspaces.md#export-and-backups)). A replace restore
(`ws_backup._replace`) decides what comes back:

- into a workspace with a `zotero_links` row (detached included): the
  backup's rows, and `zotero_state.restored_at` stamped;
- into one without: none of a link's rows (`zotero_remote.clear` and the
  Zotero conflict rows), which a later link of the workspace would
  otherwise read as its own.

The next round (`_own_library`) handles the stamp once (`status.restore_seen`):

- rows of another library (the backup of another link): wiped, and the
  workspace matched again as an existing workspace's first link, the
  restored pages winning (`adopt` `mine`);
- no rows of this library while the link has synced before, or pages
  carry a `zotero_key` of this library (a backup from before the link):
  the same, never a fill as of a new workspace;
- this library's rows: the restored pages win their differences, and
  Zotero's values stay in the conflicts for "Take Zotero's". The records,
  folders and standalone PDFs read as changed here (their `gbase` blanked:
  their projections are exact, so only a real difference goes). Notes,
  note pages and annotations keep their bases and carry `detail.restore`:
  what Zotero changed since the backup reads as changed here too, so a
  mapping Gamma reads lossily (a note's HTML, a sticky note's colour) never
  goes back for nothing, and a note whose words already agree is settled.
  The local feed starts over.

## Clock and stream

### `sync_clock`

Both engines get their rounds from `gamma/sync_clock.py`. Each registers an
`Engine` at import (`sync_clock.register`); the app imports `zotero_sync`
through its router before `sync_clock.start` runs. The link's:

```python
# zotero_sync.py, its settings from zotero_sync_rows.py: ENGINE and CLIENT "zotero", DEBOUNCE_S 3.0, WORKERS 4,
# LIVE_POLL_S 5, LIVE_SAFETY_S 30
_clock = sync_clock.register(ENGINE, rows=_rows, run=_held_round, client=CLIENT, reset=reset_interrupted,
                             debounce=DEBOUNCE_S, workers=WORKERS,
                             interval=lambda ws, poll_s: LIVE_SAFETY_S if poll_s == LIVE_POLL_S else poll_s)
```

- **Due**: a workspace gets a round when its cadence has come round since
  the last round the loop started (`interval`: Live is a safety round every
  `LIVE_SAFETY_S`, 30 s; `poll_s` 0 only by hand), or a round asked for
  with `request(ws, delay)` is due. Every committed write reaches
  `on_commit` (`ops.commit_listeners`): one that is not the engine's own
  client marks the workspace dirty (`has_local_changes`, until a round that
  started after it ends cleanly and `settle`s it) and, with `on_change`,
  asks for a round `DEBOUNCE_S` (3 s) on, so a burst of typing is one
  round.
- **Pool**: each engine has its own loop thread; with `workers` above 1 it
  hands rounds to a pool of its own (`WORKERS`, 4 links' rounds at once),
  so one link's long first fill never holds up the others. A workspace
  whose round is in the pool is passed over until it ends (its request
  stays for after it); a full pool leaves the rest due at the next look.
  The mirror keeps one worker and runs its rounds on its loop thread.
- **Never waits**: the loop takes a round lock without waiting; a round
  already running outside the loop (Sync now) is looked at again
  `BUSY_RETRY_S` (5 s) later. A request for a link detached or removed
  meanwhile is dropped once it is due.
- **Off**: `GAMMA_SYNC_INTERVAL=0` (the tests) starts no loop; rounds then
  run through the API or `sync_workspace`.

`sync_in_background` (Sync now without `wait`, a force, a link made or
reattached) asks the clock while its loop runs, so the pool bounds how
many rounds run at once; without the loop it starts one thread per
workspace, and none more while one waits for the round lock.

`sync_clock.LinkTable` is an engine's users.db table as the clock and the
engine read it, the mirror's `mirrors` and the link's `zotero_links`
alike: `save`, `status`, `patch_status` (one read-modify-write of the
status JSON at a time under the table's lock, so a round and a detach, a
force or a settings change made while it runs never overwrite each
other's keys), `stored_mode`, `rows` (the clock's rows: a row whose
workspace exists and meets `where`; the link's `where` requires a personal
workspace its owner owns) and `reset_interrupted`. At startup
`zotero_sync.reset_interrupted` marks a round the previous process left
`running` as `interrupted` (its progress gone) and asks for the next round
at once for a link whose fill went on or was cut short, when the loops
run; else it drops the fill's count. The API shows `status.fill` only
while that round is asked for or running (`zotero_sync.following`).

**Detach stops a round.** `detach_link` and `remove_link` set the stored
mode to `off`. The round's client heartbeat (`_detach_check`, at most every
`DETACH_CHECK_S`, 1 s, while the client waits on Zotero or moves a file)
raises `Detached`, and `_work` looks at the stored mode before each batch
of units; the round ends as a detached round does, its status saved.
`remove_link` then waits for the round lock before it deletes anything.

### The stream

`zotero_stream.py` makes Live links live: Zotero says a library changed,
and the link's round starts half a second later instead of at its 30 s
safety poll. One daemon thread runs an asyncio loop holding one WebSocket
to `config.zotero_stream_url()` (`wss://stream.zotero.org`,
`GAMMA_ZOTERO_STREAM_URL`); the app's lifespan starts and stops it.

- On `connected` (with `retry`, the reconnect wait the server asks for),
  it sends `createSubscriptions` with one entry per distinct key of the
  Live links (`live_links`: mode not off, `poll_s` 5, the clock's
  `where`). Each entry is the key alone, which covers every library the
  key reads.
- `subscriptionsCreated` lists each key's topics and an `errors` list; a
  key named there is dropped until its account's connection changes (its
  `connected_at`).
- `topicUpdated {topic, version}`: each Live link on that library
  (`/users/<id>` or `/groups/<id>`) whose `zotero_state.version` is below
  the event's gets `request(ws, ROUND_DELAY_S)` (0.5 s, so a burst is one
  round); other topics (`/users/<id>/publications`) are passed by.
- `topicAdded` / `topicRemoved`: the key gained or lost a library; each of
  its Live links forgets what it read of the key's access
  (`zotero_sync.forget_access`) and gets a round.
- The key set is read again every `RECHECK_S` (10 s), or at once after
  `zotero_stream.refresh()` (the link routes call it). A new key is
  subscribed on the open connection; a key no longer wanted closes the
  connection, and the next one subscribes what is left.
- A failed or closed connection is tried again after the server's `retry`,
  doubling while the tries fail, up to `BACKOFF_MAX_S` (10 minutes). A
  close code from 4400 to 4499 other than 4429 means Zotero refuses this
  client: the links on that connection get `last_error` with `error_kind`
  `auth` (`zotero_sync.record_error`, which also forgets their access), and
  no connection is made until the key set changes.

The engine is correct without the stream: its own rounds list Zotero's
changes. The stream is off when the sync loops are.

## Accounts and access

### The account's connection

A link uses its owner's Zotero API key (`zotero_account.client_for`), one
per Gamma account. Zotero's keys never expire, and `GET /keys/current`
names the key's user and access. `zotero_account.connect(user_id, api_key,
via)` checks a key with Zotero first: one Zotero does not know, or that
cannot read the person's own library, is refused (400). It then keeps the
key sealed in the `zotero-account` preference ([Data](#data)), replacing
any before; a replaced key Gamma had Zotero make through OAuth is revoked
at Zotero. `disconnect` forgets the connection, and revokes an OAuth key
(a pasted key is the person's own and stays valid there); the links pause,
each round answering `not_connected` until the account connects again. A
key the server can no longer unseal (its encryption key changed) is
`AccountError` 409, a reconnect. Deleting a Gamma account deletes its
links and its connection. Guests keep no keys: the account and link routes
refuse them (`auth.require_personal_user_id`; `GET /api/zotero/account`
answers a guest `{connected: false, can_edit: false}`); only a page's
"fetch its PDF first" is open to any member who may read the page.

A key is connected in one of two ways (`routers/zotero.py`):

- **A pasted key** (`PUT /api/zotero/account {api_key}`), made at
  zotero.org/settings/keys/new with library, notes and write access to My
  Library and read/write to groups. Always available.
- **Signing in at zotero.org** (OAuth 1.0a, whose access token *is* the
  key), in one of two modes (`oauth_mode`): `server`, this server's own
  Zotero app (`GAMMA_ZOTERO_CLIENT_KEY` / `_SECRET`, `config.zotero_client`)
  running zotero.org's steps itself (`zotero_oauth.py`: request token,
  authorize, access token); or `cloud`, Gamma Cloud's broker, which holds a
  registered app for every Gamma server without one and relays the
  approval back under a one-time code the server trades with its PKCE
  verifier and its address ([cloud_accounts.md](cloud_accounts.md#connecting-zotero)).
  Both ask for what a link needs: library, notes and write access, and
  write access to every group (`authorize_url`, the broker's `ACCESS`).
  The round trip comes back to `oauth_base`: the admin-confirmed public
  URL, else a loopback request's own origin. Anywhere else signing in is
  not offered, since an origin read from an unchecked Host header could
  send the approval anywhere.

`POST /api/zotero/oauth/start` answers `{state, url}`; the page opens
`url` and polls `POST /api/zotero/oauth/status {state}` every few seconds,
which works in a browser and in the desktop and iPad shells alike (their
navigation guards send zotero.org to the system browser). The callback
(`GET /api/zotero/oauth/callback`) needs no session, since a desktop app's
system browser has none, so it connects the account that started only in a
browser shown to be that person's; otherwise whoever started could pass
the approval link to someone else and receive their key (OAuth 1.0's
session fixation):

- a browser signed in to another Gamma account is refused;
- a start sets a cookie (`gamma_zotero_oauth`, a nonce whose SHA-256 the
  pending record keeps), and the browser carrying it connects;
- any other browser gets a page naming the Gamma account and server that
  would receive the key, whose Connect button posts a one-time token
  (`/api/zotero/oauth/confirm`). A session alone proves nothing: a login
  CSRF can plant one.

**The desktop loopback rule.** Only on the desktop app's own server
(`config.folder_links_enabled()`, which only the app's sidecar sets) does a
round trip started from this machine on a loopback base connect without
the cookie: there a loopback peer is the person at their computer, whose
system browser holds no cookie of the app's web view. On any other server
a loopback peer proves nothing: behind an SSH tunnel or a port forward,
every person's requests come from loopback.

The addresses and the server's own Zotero app are configuration
(`config.zotero_*`): `GAMMA_ZOTERO_API_BASE` (the Web API,
`https://api.zotero.org`), `GAMMA_ZOTERO_WEB_BASE` (zotero.org, for the
OAuth steps and a link's `remote_url`), `GAMMA_ZOTERO_STREAM_URL` (the
streaming API) and `GAMMA_ZOTERO_CLIENT_KEY` / `GAMMA_ZOTERO_CLIENT_SECRET`.
The fake sets the first two for its tests.

### What the key may do

`ZoteroClient.libraries()` reads the key's access (`keys/current`) and its
groups: `{id, name, kind, user_id, read, write, files, notes}` per library.
In My Library each of `write`, `files` and `notes` needs `read`
(`my_library_access`). In a group, `read` is the key's grant for it (or
for all groups), `write` the grant's write access and the group's
`libraryEditing` letting this member, `files` the group's `fileEditing`
letting this member, and `notes` comes with reading it. The round reads:

- **read**: without it the library is `library_gone`;
- **write**: without it a two-way link's rounds receive only (the pill
  says "receive only · your Zotero key cannot write"), and a force push is
  refused;
- **files**: downloads and uploads (`fetchable`, `may_send_files`);
  without it PDFs wait (`no_access`) and the pill offers Reconnect beside
  Sync;
- **notes**: without it no note travels either way.

Access granted later is taken up by the next round that reads the key
again ([The round](#the-round)); the stream's `topicAdded` and
`topicRemoved` make that the next round.

### Refusals

`create_link` talks to Zotero before it makes anything, so a missing
connection (409), a key Zotero refuses (409), Zotero unreachable (502) or a
library the key does not read (400) fails first. It then refuses (400),
under one lock with the insert (two requests at once make one link):

- a library this owner already links (`LINKED_ALREADY`; the unique index
  is refused the same way);
- a `workspace_id` that is not a personal workspace the owner owns, one
  with a `mirrors` row (a clone, a workspace that publishes pages, either
  detached included), or one linked already. The row a publication keeps
  once its last page was unpublished (`page_filter` `'[]'`) is no bar, as
  for `workspaces.update`: once the link's row is written, `create_link`
  forgets that publication under its round lock
  (`sync_engine.remove_mirror`: the `mirrors` row and its `sync_pages`,
  `sync_conflicts` and `sync_log` rows), so the link and a publication
  never share those tables. Nothing is lost: the publication held no
  page, publishing is refused in a linked workspace, and the account's
  publishing token is shared, so nothing is revoked; the empty workspace
  stays on the share host.

A new workspace made for a link that then fails is deleted with it. Other
refusals keep a linked workspace a link's:

- `sync_engine.create_mirror` and `publish.publish` refuse it (and
  `publish.state` says why: `ZOTERO_LINKED`);
- `workspaces.update` refuses changing its kind while a `zotero_links` row
  keeps it (detached included);
- Import → Zotero library refuses an RDF export into it, 409 "This
  workspace is kept in step with {library}; its items arrive by
  themselves" (`routers/imports.py` `_refuse_linked`): the import's copies
  would come back to Zotero as doubles of the annotated ones
  ([import_export.md](import_export.md#zotero-library-import));
- the embedded-annotation import skips an annotation whose Zotero key the
  workspace's link keeps a row of;
- a person's PDF is refused on a page whose Zotero item's PDF is coming
  ([PDFs](#pdfs));
- a link whose workspace is no longer a personal one its owner owns gets
  no rounds (the clock's `where`), and `_access` refuses it as
  `not_connected`;
- `set_settings` refuses a direction change while detached, and
  `force_sync` a force push on a receive-only link.

## Routes

The link API is `routers/zotero_sync.py` under `/api/zotero/links`, shaped
as the clone API (`routers/mirrors.py`): session only (an integration
token is refused: these are the account's), personal accounts only, the
link's owner only (404 for anyone else), every route a sync `def`. The
account routes are `routers/zotero.py`. The full contract is the
"Zotero" section of
[api.md](api.md#zotero-routerszoteropy-routerszotero_syncpy--see-zotero_syncmd).

| Route | What it does |
|---|---|
| `GET /api/zotero/links` | `{links: [info…]}`, the caller's |
| `POST /api/zotero/links` | `{library, workspace_id?, name?, mode, download, upload, auto_tags, send_unrecorded, adopt}` → 201 `info`; the first round runs in the background |
| `GET /api/zotero/links/preview?library=&workspace_id=` | the link dialog's counts ([First link and force](#first-link-and-force)); 429, 503 |
| `GET /api/zotero/links/{ws}` | `info` |
| `PATCH /api/zotero/links/{ws}` | `{poll_s?, on_change?, mode?, download?, upload?, auto_tags?, send_unrecorded?}` → `info` |
| `POST /api/zotero/links/{ws}/sync?wait=` | Sync now (lifts a wait on Zotero's full storage): `{status, conflicts_open}` |
| `POST /api/zotero/links/{ws}/fetch` | `{page_id}`: the page's PDF first (any member who may read the page) → `{queued: true}` |
| `POST /api/zotero/links/{ws}/detach`, `/relink` (no body), `/force` `{direction}` | `info` |
| `DELETE /api/zotero/links/{ws}` | forget the link; the workspace and the library stay → `{ok: true}` |
| `GET /api/zotero/links/{ws}/log?limit=` | `{changes: [...]}`, the mirror's log rows |
| `GET /api/zotero/links/{ws}/conflicts?resolved=&page=` | `{conflicts: [...]}` |
| `POST /api/zotero/links/{ws}/conflicts/{id}` | `{choice: keep \| mine \| theirs}` → `{id, resolved: true}`; 404; 409 |
| `GET`, `PUT`, `DELETE /api/zotero/account` | the account's connection (`status`, never the key, with `can_edit` and `oauth: {available, mode}`), a pasted key, a disconnect |
| `GET /api/zotero/libraries` | the libraries the key reaches, with what it may do in each |
| `POST /api/zotero/oauth/start`, `/status`, `/confirm`; `GET /api/zotero/oauth/callback` | the sign-in round trip |

`info` is `{kind: "zotero", workspace_id, name, library, library_name,
remote_name, remote_url, mode, download, upload, auto_tags,
send_unrecorded, poll_s, on_change, status, created_at, conflicts_open,
conflicts_newest, pending_local, detached, interval_s}`; `remote_url` is
the library on zotero.org (`/<username>/library`, `/groups/<id>/library`).
`/api/workspaces/mine` and the session's workspace rows carry `zotero_of`
(the library's name while a link is not detached) and `zotero_detached`.

## Frontend

A Zotero link reuses the clone's components with `kind: "zotero"`
([mirror.md](mirror.md#what-the-person-sees)); `collaboration/syncLinks.js`
holds every reading only a Zotero link has, pure so node tests it.

- **Which link.** `LINK_BASES` and `linkPath` say which API a link lives
  under (`/mirrors` or `/zotero/links`), `linkKind` which kind an info is,
  `workspaceLink` the open workspace's (from `zotero_of`). `pillLink` keeps
  the pill of a link detached from the pill itself until a reload, a
  workspace switch or its removal (a detached link lists as an ordinary
  workspace); `linkGone` clears a removed link's fields from the session
  row.
- **The pill** (`MirrorPopover.jsx` `mirrorState`). A Zotero link reads
  `status.error_kind`, never the sentence (`zoteroError`): "Zotero
  unreachable · your edits wait here" (`offline`), "Reconnect Zotero" with a
  button to Settings › Account & sync (`auth`, `not_connected`), "This key
  no longer reaches {library}" (`library_gone`), "Zotero is busy ·
  retrying" (`busy`). An error only a new connection mends reads before the
  conflicts (`zoteroHalted`). While a fill runs, and between two bounded
  rounds of it (`zoteroFollowing`), it reads "First sync 300 / 2,000" with
  "PDFs 1.2 GB / 8.0 GB" (`zoteroRunText`, `zoteroBytesText`). Changes still
  queued read "N changes queued" (`zoteroQueued`), never "Up to date"; what
  waits outside Gamma reads in warn before "Up to date" ("3 PDFs not in
  Zotero Storage", "Zotero storage is full · 2 PDFs wait",
  `zoteroWaitingState`), and what waits in the ordinary course goes in the
  tooltip (`zoteroWaitingNote`: "1 PDF replaced here waits while its
  highlights are placed in it; then it goes to Zotero" for
  `files.placing`). The up-to-date tooltip says when Zotero is next asked
  (`zoteroCadence`): Manual is no schedule, Zotero's changes coming with
  the next round, on Sync, after an edit (while `on_change` is on) or for
  a change still queued. The key's limits are tags and sentences
  (`zoteroLimits`). `unsentEdits` counts `pending_local` as on its way only
  while the last round left nothing queued. The pill reads the link every
  20 s, and every 2 s while a round runs, an edit is on its way or the
  link has never run (`linkPollMs`, `zoteroUnstarted`: not detached, no
  `running`, `last_sync` or `last_attempt`), so a link just made does not
  stand at "Not synced yet" until the slow read; a link whose first round
  ended on an error (`last_attempt`) is read every 20 s.
- **The pill's settings** add, for a link, "Automatic sync" ("when
  Zotero's changes come in": the cadence tiles with Live, `ZOTERO_LIVE_S`,
  and Manual, "No schedule", `ZOTERO_CADENCE_TITLE`), "Sync after an edit"
  (`onChangeHint`: a round 3 s after the last change, `ZOTERO_DEBOUNCE_S`,
  and a page's "Notes from Gamma" once the page has been still 60 s,
  `ZOTERO_NOTES_QUIET_S`; a clone's comes about a second after an edit),
  Direction, "PDFs from Zotero" (All / When opened), "Send PDFs to Zotero"
  (`ZOTERO_UPLOAD_HINT`, `ZOTERO_UPLOAD_TITLE`: it decides every PDF that
  goes up, a paper's first and one replaced here alike, but none goes over
  or beside a Zotero PDF that is a linked file or not in Zotero Storage;
  off, a PDF replaced here parks and its annotations wait), "Automatic
  tags as labels" and "PDFs without a record" (`ZoteroLinkSettings`),
  Force pull / Force push, Detach / Reattach and Remove link, with the
  titles of `LINK_ACTIONS.zotero`. A force asks first, in the pill's
  inline Yes / No and in Settings' confirm box alike
  (`zoteroForceConfirm`): where each side's trash goes (a pull also
  deletes here the notes and annotations Zotero trashed, edited here or
  not, and keeps a page in Recently deleted 30 days), what a push still
  takes from Zotero (`ZOTERO_ADOPT_LIMITS`: a note Gamma cannot write
  back, a PDF whose Zotero annotations Gamma cannot carry over), that what
  it replaces is counted in the log and not listed under Conflicts (a
  Zotero PDF a push writes over excepted), and that Gamma keeps no
  earlier version of a page, which only a backup made before brings
  back. A refused change shows in an alert, with Reconnect when a new
  connection mends it.
- **The log** names a link's actions in its words (`ZOTERO_LOG_ACTIONS`).
- **Settings → Account & sync → Zotero** (`SettingsZotero.jsx`): the
  account row, its Zotero user and what the key may do, whether Zotero
  still takes the key (it asks for the libraries), Connect Zotero (the
  sign-in, where offered) and "Use an API key…", Connect again, and
  Disconnect ("Your Zotero links pause until you connect again; nothing is
  deleted.").
- **Settings → Workspaces → Clones and Zotero links**
  (`SettingsMirrors.jsx`): the links after the clones, a row each, with the
  pill's reading, tags and actions ("No clones or Zotero links yet." when
  there are none); "Link a Zotero library…" opens the link dialog. A
  link made there closes Settings and opens its workspace
  (`switchWorkspace`, a navigation whose session read lists the new
  workspace), where the pill follows the first round; a link into the
  workspace already open reads the session again instead, so its pill
  shows at once. Its two create buttons wait until the account's clones
  and links are read, and Into offers a workspace no clone, publication or
  link holds
  (`linkCandidates`: the session row's `mirror_of`, `mirror_detached`,
  `publishing`, `zotero_of` and `zotero_detached`, and the lists' rows; a
  publication emptied of its pages holds none for a link, as
  `create_link` reads it, while a clone refuses any `mirrors` row).
- **The link dialog** (`ZoteroLinkDialog.jsx`): connects first, in place,
  when the account has no key (the sign-in or a pasted key; it asks for
  the account again every few seconds, so a connection made elsewhere
  carries it on), then lists the libraries the key reaches, counts what a
  link would move (`zoteroPreview.js`: "at least" while `partial`, "too
  large to compare in advance" when the match counts are null; a busy
  count asked again after 2, 4 and 8 s, then Try again; linking never
  waits for it), and offers Into (a new workspace or an existing one, and
  which side wins where a page differs, `zoteroAdoptHint`: the log counts
  what it replaces, a Zotero PDF Gamma's goes over is kept under
  Conflicts, and `ZOTERO_ADOPT_LIMITS`), the name (`zoteroWorkspaceName`:
  "Zotero" for My Library, a group's name, as `create_link` names it), the
  direction (receive only when the key cannot write), which PDFs come
  ("When opened" by default above 1,000 PDFs, for a partial count and on
  the iPad's own server), "Send PDFs to Zotero" (off by default into an
  existing workspace), automatic tags and PDFs without a record.
- **Conflict cards** (`MergeResolver.jsx`, `conflictKinds.js`): the sides
  are Gamma and Zotero; a Zotero kind's card shows the row's `result`
  where it says why (`zoteroReason`), says "kept in Gamma … goes to Zotero
  once the link sends changes again" for a receive-only row (`keptHere`,
  which matches `KEPT_HERE`'s end), offers "Send to Zotero anyway" for a
  `zotero_lossy` row, and opens a folder or label row on its folder or
  label (`conflictPlace`). A page-level row (`block_id` the page id: its
  record) is a chip on the page's record header. A link's conflict list
  has "Keep all".
- **The PDF card** (`ZoteroPage.jsx` `ZoteroPdfCard`, `syncLinks.zoteroPdfCard`):
  a page with `zotero_pdf` and no PDF shows "PDF in Zotero · {size}", its
  state, Download now (a live link's, for a PDF that can come), or why it
  cannot come. It takes the attach prompt's place exactly while the server
  would refuse an attach (`zoteroPdfSource`: a live link, a detached link,
  a live or detached clone); a PDF that cannot come, or that nothing brings
  any more, sits above the prompt. Its note says what becomes of a copy
  attached there: while the PDF waits on something that may pass (a failed
  download, a key without file access, this server's full storage:
  `failed`, `files`, `quota`), the copy goes to Zotero, with "Send PDFs to
  Zotero" on, once Gamma can read Zotero's file, which is then kept here
  under Conflicts (the `both` plan); for a linked file, one not in Zotero
  Storage, or a page nothing brings a PDF to any more, it stays in Gamma.
  Opening such a page asks for its PDF once (`fetchZoteroPdf`). An attach the server
  refuses with `ZOTERO_PDF_COMING` is read as such (`isZoteroPdfComing`).
- **The read-only badge** (`ZoteroReadonlyBadge`): a block with
  `zotero_readonly`, "Zotero note with citations: edits stay in Gamma" or
  "{author}'s annotation: read-only in Zotero"; its editor warns once per
  region and session before the first edit (`warnOnce`,
  `editor/BlockTree.jsx`).
- **The notice**: `app/notices.js` names `zotero-conflicts` ("Resolve the
  conflicts").
- **The desktop bar** (`desktop/lib/keeping.js`, `desktop/main.js`
  `refreshKeeping`): each local server's links (`GET /api/zotero/links`)
  are items of kind `zotero` beside its clones and folders on disk, read as
  the pill reads them; pause detaches, resume reattaches, sync runs a round
  ([mirror.md](mirror.md#the-desktop-switcher)).

## Limits and known gaps

- **Files outside Zotero Storage.** The Web API serves only Zotero
  Storage: a WebDAV user's PDFs and linked files never come, and their
  annotations stay in Zotero. Zotero 10's local API serves them on the same
  machine, which only the desktop sidecar could reach; it is not used
  ([research note](../research/zotero-sync.md)).
- **One PDF a page.** Only the primary attachment travels; snapshots,
  second PDFs and their annotations, EPUB and web-snapshot annotations
  stay in Zotero. A Gamma PDF goes up as a new attachment only for an item
  with no PDF attachment at all, or beside a file whose annotations Gamma
  cannot move: highlights a replacement left dashed or lost count as
  such, while those still being placed make the PDF wait
  ([The plan](#the-plan)). "Take Zotero's" of a PDF replaced on both
  sides that went beside Zotero's puts the page back on Zotero's PDF, and
  Gamma's attachment stays in Zotero, no page's PDF; a highlight made here
  meanwhile is under that attachment too, besides Zotero's own, as at any
  change of the primary.
- **A PDF removed here** sends nothing: Zotero's attachment stays, and the
  page shows its card again (`kept_out`), the file coming only when asked.
- **Note shapes.** A Zotero note's paragraphs come back as list items once
  Gamma sends the note (`note_html`'s shape). A lossy note whose first line
  is deleted here is rooted again on its next line, and the lines lifted
  out go with the page's own notes, so they also stand in "Notes from
  Gamma" beside the lossy note Zotero still holds.
- **Lossy mappings** are kept apart, not carried: a turned text annotation
  stands upright here; a text box's Markdown goes as plain text, so bold,
  lists, code and math draw here and can move a line break, a `~~~` fence
  is code here and text in Zotero, and an @mention is a chip here and its
  raw `@…` there; a text annotation's leading spaces and runs of spaces,
  which Zotero keeps, collapse in its box; an ink item has one colour and
  one width (pressure, timing, opacity and grain stay Gamma's); a
  passage's `nextPageRects` and an annotation's tags stay in Zotero. A
  text box's blank lines travel both ways, each a line in both apps.
- **A text box's lines** break where Zotero's reader breaks them when both
  apps resolve the reader's font stack to the same face: measured on
  Windows (Segoe UI) only. macOS, Linux and the iPad are not verified
  ([text_boxes.md](text_boxes.md#zotero-boxes)).
- **The sort index's offset** comes from MuPDF's characters, not pdf.js's,
  and can miss by a few characters on a page the two read differently.
- **Page labels Zotero infers.** Gamma's labels are pdf.js's, as the web
  library's reader reads them. Zotero 9's desktop reader labels its own
  annotations through its pdf.js fork's `getPageLabels2`: the PDF's labels
  only when a page number printed on one of the first 25 pages matches one
  (or none is printed, or a roman one is), else the printed arabic
  numbering carried over every page ("-" below 1), else page numbers; and
  a new annotation takes the offset of the last numeric label on an
  earlier page. The reader's master, while its structured document is on,
  takes the PDF's labels when they cover every page, else the printed
  numbers (`addPageLabels`). An annotation made in Gamma can so read
  another label in Zotero than one made there on the same page (a journal
  PDF without `/PageLabels`: "1" against the printed "523"); Gamma never
  sends over either.
- **Label trees read otherwise.** An inline kid is read as pdf.js's master
  reads it; pdf.js 5.4.0 (Zotero 9) and 4.4.168 (Gamma's viewer) take an
  inline kid of an inline root, or a second inline kid, for a repeat and
  read no labels. Labels come from the objects MuPDF parsed, so where its
  parser and pdf.js's differ the labels may too: a number with an exponent
  in `/St` (no number to MuPDF, 10 for `1e1` to pdf.js), an odd last hex
  digit, a CR or CRLF inside a literal string, a `/St` past 2^53, or one
  so large that pdf.js runs out of string length. An annotation Gamma
  created under a random key (after a key collision, or anew after Zotero
  removed it for good) is not recognised as Gamma's, so a label its create
  sent undecoded stays.
- **Another member's annotations** in a group library are read-only here,
  and everything Gamma writes with a key is that key's user's in Zotero.
- **Resolved conflict rows** are kept but no frontend surface lists them:
  the sentences of the rows a first link or a force decided are seen only
  through the API.
- **Live** needs the streaming API; without it (refused, or off) Zotero's
  changes arrive at the 30 s safety round.
- **The preview** counts at most 20 pages of attachments and compares at
  most 2,000 items; beyond that it says "at least" or nothing, and the
  first round pairs everything all the same.
- **The desktop app at launch** starts the local servers that hold a
  Zotero link that is not detached, as it does the clones' hosts and the
  folder host (`startBackgroundHosts`, `registry.launchHosts`), from what
  its last reading of each running server's links recorded
  (`zoteroHost`, [architecture.md](../../desktop/docs/architecture.md#kept-on-this-computer)).
  It reads them as that server's seeded admin, so a link another account
  made there does not count, and a link made in Settings counts from the
  shell's next reading (3 s after launch, then every 15 s): a server quit
  before that starts at the next launch only once it is opened.
- **A note page sent back** after Zotero removed its note for good takes
  no pictures along (they stay as Gamma's blocks), and a standalone PDF's
  page that stays here unlinked keeps its old `zotero_key`.
- **A first link's read** keeps its place while items are edited or
  trashed, but an item removed for good from Zotero's trash while the
  pages are read can still shift them and leave one item unpaired, made a
  page of its own beside the page it should have paired with.

### Known defects

This one waits for a fix in the code:

- **A force push's log line and lossy notes.** `_decided_note` takes its
  word from the round's `adopt`, so a force push that took Zotero's
  version of a lossy note counts it under "replaced there", and the open
  `zotero_lossy` row of that note stays open, showing Gamma's replaced
  text.

## Tests

The engine's tests run against an in-memory Zotero; nothing in them
reaches api.zotero.org (`tests/conftest.py` strips every `GAMMA_ZOTERO_*`
variable, and the fake's storage refuses Zotero's headers, so a client
that leaked the key would fail its test).

- **`tests/zotero_fake.py`**: `FakeZotero`, the Web API v3 as the link uses
  it, zotero.org's OAuth steps and the file storage, with Zotero's
  versions and refusals checked against the real API (the research note's
  "Verified against the real library"): per-library access (a key without
  notes access sees no note), 100 objects a read and 50 a write, object
  and library versions (428, 412, `version: 0`), `If-Modified-Since-Version`,
  `since`, `format=versions`, `itemType` filters, `includeTrashed`, the
  annotation rules, group members (`meta.createdByUser`), the trash and
  `/deleted`, the attachments' shapes (stored, linked, WebDAV, a note's
  pictures, zipped snapshots), the upload steps and the quota.
  `install(monkeypatch)` points gamma at it (`zotero_web._request`, the
  throttle's sleep made instant); builders change the library as a
  desktop would (`add_item`, `add_file`, `edit`, `trash`, `restore`,
  `delete`, `acting_as`); `fail_next` and `on_request` inject failures and
  changes mid-round; `calls` and `sent` record every request. `serve()`
  runs it over HTTP for the browser suite (`python -I
  backend/tests/zotero_fake.py --port 0`).
- **`tests/zotero_fixtures.py`**: a two-page paper PDF typeset with a real
  text layer, the rectangles over its text in user space with what lies
  under them, `standard_library` (one of each kind of thing the engine
  meets, the shapes Zotero's reader writes) and `generated_library` for
  scale.
- **`tests/zotero_idle.py`**: after a test's rounds, one more round writes
  nothing on either side: no write request, no op logged, no change in the
  feed or the blocks (`Idle.check`). A test that leaves a link unsettled
  on purpose says so with `idle.skip(reason)`.
- **`tests/zotero_engine.py`**: the shared harness, imported by name by
  each engine file (never through `pytest_plugins`, so its autouse
  fixtures stay in the files that import them): `owner_id` and `me` (the
  importing module's `USER`, signed in with Zotero connected, every link
  checked idle and removed after the test); `background` (autouse: rounds
  run only when a test asks, the budget lifted, background rounds
  recorded); `no_relocation` (autouse: relocation runs recorded, not run);
  `quiet` (opt-in: the page's own notes go without their 60 s); `bounded`
  (opt-in: a round capped at 300 units, so a loop fails fast); `fake`,
  `made`, `idle`, `held` (the units stubbed to pass everything over, so a
  test reads what the listings queued); helpers to link, sync through the
  API, force, and read rows, queue entries (`_due_now`), conflict counts
  and pages and blocks, and `asked_soon` (whether a round was asked for
  at once).

| Suite | What it covers |
|---|---|
| `test_zotero_sync.py` | the registry and API, the dialog's counts, conflicts, clock and status, listing and the feed, bounded rounds |
| `test_zotero_sync_folders.py` | collections ↔ folders |
| `test_zotero_sync_items.py` | first fill, records, retypes, trash and removals, both-sides and field conflicts, lost creates, re-parenting, the change-feed skip |
| `test_zotero_sync_pdfs.py` | downloads, uploads, both sides replaced (in place and beside, with and without a copy, a lost answer, Take Zotero's back on Zotero's PDF), kept copies and the upload GC, full storage (a download it holds still counted when its item changes again), hashing, transfers, the wait while highlights are placed (stubbed and the real relocate job) and going in place once they are, a new attachment decided afresh, standalone PDFs removed for good |
| `test_zotero_sync_first_link.py` | the first link of an existing workspace, its records and PDFs, force |
| `test_zotero_sync_link.py` | a first link or force spent with PDFs held by full storage (here and Zotero's), a pair refused or held keeping its side, a force pull after a push, a log line per round, an annotated PDF in a push, a read edited between two of its pages (items and notes), an emptied publication forgotten, a reattach keeping a first link's side |
| `test_zotero_sync_words.py` | what the link's words on screen say it does: a force push taking Zotero's lossy note and annotated PDF, a force pull deleting a trashed note edited here, a replaced PDF on a linked-file paper staying here, a copy attached while Zotero's PDF cannot come, Manual still syncing after an edit |
| `test_zotero_sync_annotations.py` | annotations, force, restore, receive only, web snapshots, a waiting annotation Zotero trashes (and restores), page labels (a label a create sent undecoded corrected once by a plain round, Zotero's own and another member's kept, the PATCH of the label alone or with an edit), text boxes (a Gamma box framed on its first send, fixed, auto and with a blank line; a linked box without the key, stored so or written back so, gaining it with no request, a blank line in its text and a trash in Zotero among them; a refused create and a restore sent from the converted box; the width mode on a pull and on a merged text) |
| `test_zotero_sync_notes.py` | notes, a first line deleted, notes access granted later, note pages, a note page changed here whose note Zotero removed for good |
| `test_zotero_sync_large.py` | scale: a long fill over many rounds, a 2,000-item library within the budget, a 3,000-block page, the queries' plans (`ZOTERO_MEASURE=1` prints timings and memory) |
| `test_zotero_sync_hardening.py` | Gamma edits made while a unit decides (the compare-and-set), Zotero's library moving at each step of a round, receive-only rounds, another member's annotation |
| `test_zotero_sync_crashes.py` | a round killed after (or before) each kind of write, after a download stored and before it was attached, after an upload sent and before it was registered: the next round converges, compared with a run never killed |
| `test_zotero_sync_fuzz.py` | random edits on both sides with rounds between them at random (PDFs replaced on either side, relocations running, writing and ending, note pages removed for good among them; text boxes made in Gamma, auto and fixed, and text annotations made in Zotero, retyped with and without blank lines, resized, moved in Zotero, and written back here without their `zotero` key), now and then receive only, detached, a force, a killed round: once settled (nothing due, no round asked for at once, rounds capped at 400 units) both sides hold the same, every text box on a live page is a Zotero box with one live row whose comment, rect and font size are the box's, each paper's PDF is one attachment holding one file, three more rounds write nothing and add no conflict, and no new attachment was made beside Zotero's PDF while its highlights were placed or when each of its annotations could move (`ZOTERO_FUZZ_SEEDS`, `ZOTERO_FUZZ_STEPS`, `ZOTERO_FUZZ_TRACE=<key>`) |
| `test_zotero_merge.py`, `test_zotero_annotations.py`, `test_zotero_notes.py`, `test_zotero_fields.py`, `test_zotero_local.py`, `test_zotero_remote.py`, `test_zotero_web.py` | the pure rules and the I/O layers on their own (the sort index against the reader's values for the fixture PDF, the field mapping per type, note regions) |
| `test_page_labels.py` | `pdf_text.read_page_labels` against pdf.js's rules on PDFs written in the test (prefix encodings, styles, `/St`, Kids and the order they are read in, malformed trees, an encrypted file, a 3,000-page tree) |
| `test_zotero_account.py`, `test_zotero_oauth.py`, `test_zotero_routes.py`, `test_zotero_stream.py`, `test_sync_clock.py` | the connection, OAuth, the routes and their refusals, the stream, the clock and its pool |

Each engine file has its own account (`zs_` the registry and API, `zd_`
folders, `zi_` items, `zp_` PDFs, `zk_` first links, `zlk_` the link's
lifecycle, `zw_` the words, `za_` annotations, `zn_` notes, `zg_` scale,
`zh_` hardening, `zc_` crashes, `zf_` the fuzz),
and `owner_id` fails when two files claim one. Run them with `--dist loadfile`, as the full suite
does ([debugging.md](debugging.md#full-backend-suite)); the fuzz takes more
seeds with `ZOTERO_FUZZ_SEEDS=400` and `-n 8`. The consumers have their
own tests: `test_migrations.py` and `test_lazy_migrations.py` (step 45),
`test_token_account_scope.py`, `test_upload_gc.py`, `test_ws_backups.py`,
`test_workspaces.py`, `test_mirror.py`, `test_notices.py`,
`test_inbox.py`; the broker, `cloud/tests/test_zotero.py`.

Frontend: `tests/syncLinks.test.mjs`, `tests/conflictKinds.test.mjs` (it
reads `CONFLICT_KINDS` and `KEPT_HERE` from the engine's source, so a kind
the engine adds needs its words), `tests/zoteroPreview.test.mjs` (it reads
`preview_first_link`'s keys), `tests/notices.test.mjs`,
`tests/settingsReads.test.mjs`; the desktop's `desktop/test/keeping.test.js`
(the bar's reading), `desktop/test/registry.test.js` (which servers start
at launch) and `desktop/test/main.test.js` (the launch record a reading
writes, one start per server).

The browser scenario `frontend/tests/e2e/scenarios/zotero-link.mjs` (group
`zotero-link`, [debugging.md](debugging.md#browser-end-to-end-suite)) runs a
Gamma against the fake over HTTP, with a streaming address that refuses
connections: an account connects with a pasted key, links My Library into
a new workspace from the Clones and Zotero links section, which opens that
workspace, its pill showing the first sync from its first read, and syncs
it; the pill waits on a linked file until Zotero trashes it; the paper has
its PDF, highlight, ink and text box, the collections and tags came as
folders and labels; a highlight's note edited here reaches Zotero, a
colour changed in Zotero comes back; its text annotation is a Zotero box
that shows the lines a textarea styled as Zotero's reader shows at its
rect, and a box typed on the paper is one from its first keystroke, sent
on Sync now with the box as its rect, an auto box 1 pt wider than its
text; a record field both sides changed is
a conflict resolved with Zotero's value; a group library linked "When
opened" into the open workspace closes Settings on it, gives it its pill
at once, and shows its paper's PDF card until the page, opened, brings the
PDF, and another member's annotation shows read-only; a paper whose PDF is
a linked file takes a PDF attached in Gamma; the pill's settings detach
the link and, once the account is connected again, reattach it; Settings
removes it.
