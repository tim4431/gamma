# Disk to Gamma: PDFs that change on this computer

Designed in October 2026, after the one-way folder sync shipped
([folder-sync.md](folder-sync.md), built as
[dev/folder_sync.md](../dev/folder_sync.md)). The question was how a PDF
that changes on a PC reaches Gamma: a manuscript compiled again from its
TeX, a paper annotated in another app. Status: **built** (2026-10-30):
steps 1 and 2 below, and the two retention rules of step 3. The mechanics
are in [dev/folder_sync.md](../dev/folder_sync.md) ("Sending PDFs back"),
[dev/user_db.md](../dev/user_db.md) ("Stored files") and the desktop's
[architecture.md](../../desktop/docs/architecture.md) ("Folders and files
on this computer"). This note keeps the reasoning; "As built" at the end
lists where the build departs from it.

## Two stories, one mechanism

1. **A manuscript.** The page *Draft on X* holds the PDF of my own paper.
   `latexmk` writes `D:\tex\draft-x\build\main.pdf` on every save. After
   each build Gamma should show the new file, with my highlights moved to
   where their text now is and my notes untouched.
2. **A kept folder.** *My papers* is kept on disk by the desktop app. I open
   one of its PDFs in another reader, add a note in the margin, save. Gamma
   should pick the file up, and its embedded annotations with it.

Both are the same act, a PDF on disk becoming a page's new file, and the
server already has the primitive for it: `PUT /pages/{id}/attachment`
replaces a page's PDF, keeps its id, notes and highlights, dedups by hash,
starts the relocation of highlights and switches the open viewers. The API
doc even shows a build script doing it by hand. What is missing is the part
that watches the disk and calls it, and the places in the app where a
person asks for that.

The kept folder alone serves the manuscript poorly: it writes the paper as
`<Title>.pdf` and renames it when the title changes, while a build writes
`main.pdf` where the build lives. A person would have to copy the output
over the kept file after every compile, and the first rename would break
it. So there are two doors, on one engine.

## The model

A **link to disk** pairs something in Gamma with something on this computer
and says which way bytes move:

| Kind | In Gamma | On disk | Pull (Gamma → disk) | Push (disk → Gamma) |
|---|---|---|---|---|
| folder link (built) | a folder, or `root` | a directory | always | opt-in, PDFs only |
| file link (new) | one page | one `.pdf` file | never | always |

Nothing else changes in the model. A link is still a row of users.db kept
by the desktop app's own local server, run by the same tick, shown in the
same panel. The notes file stays one way: its way back is a merge problem
of another size and stays open in [folder-sync.md](folder-sync.md).

### Identity decides everything

A stored PDF's `doc_id` is the first 24 hex characters of its SHA-256
(`storage.content_digest`), so a client can hash a file on disk and know,
without a byte sent, whether it is Gamma's file. Each PDF the engine tracks
has an **entry**: `{page, doc, size, mtime}`, what a folder link's state
file already records per path, and what a file link keeps in its row. Three
questions, each one comparison, decide what a round does with a PDF:

- *moved*: does the file's size or mtime differ from the entry's? (a stat)
- *same bytes*: does its hash equal the entry's `doc`? (asked only when moved)
- *Gamma moved*: does Gamma's current `doc_id` for the page differ from the
  entry's `doc`? (the manifest, or the page itself for a file link)

| moved | same bytes | Gamma moved | the round |
|---|---|---|---|
| no | – | no | nothing |
| no | – | yes | pull, as today |
| yes | yes | no | refresh the entry's stat; nothing travels |
| yes | yes | yes | refresh the stat, then pull: the file is still the old version |
| yes | no | no | **push**: upload, replace, record the new `doc` and stat |
| yes | no | yes | **both changed**: keep the file, report it |

A file link has no "Gamma moved" column: disk wins by definition, with one
guard. A change made in Gamma by hand (the published version put in place
of the draft) is not undone by the next round, only by the next *build*:
the push waits for the disk to move again. "Follow" means every new build
is shown, not that nothing else may be.

Both changed, in a kept folder, is left as today's "changed on disk" with a
clearer reason, and needs no new UI to resolve: *Sync* with its existing
replace question lets Gamma win, and the page's *Replace the PDF…* with the
disk file lets disk win.

### A file is pushed only when it is whole

A compile writes the PDF over a second or more, and `latexmk` may write it
three times in a minute while BibTeX runs. The engine treats a moved file as
**settled** when its mtime is at least `SETTLE_S` (3 s) old, it opens for
reading (Windows refuses while the writer holds it), and `%%EOF` is in its
last kilobyte. An unsettled file is not an error: the round says *waiting
for the file to finish* and the next tick asks again. An intermediate build
that does settle is pushed and replaced by the next; harmless, one more
relocation run.

## The engine

`gamma_sync.py` stays the one engine both hosts run, and gains the mirror
image of what it has. A round reads through a **source** (`manifest()`,
`notes()`, `download()`) and now writes through a **sink**:

```text
Sink
  store(path) -> doc_id          the file's bytes stored under their hash (a no-op for known bytes)
  replace(page_id, doc_id, name) -> {doc_id, job?, relocation_error?}
  current(page_id) -> doc_id     what the page shows now (a file link's "Gamma moved")
```

- `RemoteSink` answers over HTTP: a multipart `POST /api/uploads` (stdlib,
  a boundary and two parts; `/uploads/parts` for a file past the proxy
  cap, later), `PUT /api/pages/{id}/attachment`, `GET /api/blocks/{id}`.
  It needs a **write** token; `/api/sync/whoami` says the scope.
- `LocalSink` (in `folder_links.py`, beside `LocalSource`) stores with
  `storage.store_pdf_stream` from the open file, which spools, hashes,
  checks the quota and the `%PDF` header exactly as the upload route does,
  and replaces through `pages.replace_attachment` (below) as the account
  that made the link.

`Push` is one step with one job: given entries and a sink, apply the table
above to each, return the entries as they now are plus counts (`sent`,
`waiting`, `kept`). It never looks at a manifest. The `Round` calls it
*first*, so the manifest it fetches next already names the new `doc_id`
and the pull leaves the pushed file alone. A file link's round is the push
step and nothing else. The client gets `sync --push`, `init --push`, and a
`push FILE --page ID [--watch S]` command for a build script or a CI job:
hash, compare with the page's `doc_id`, upload and replace when they
differ, exit 0 either way. No state file: the page is the state.

Errors stay per file, never per round. A 409 (the bytes belong to another
page: someone saved paper B over paper A), a 507 (quota), a refused token
and a page that no longer exists are *kept* entries with their reason,
listed in the status as the kept files are today.

## The server

**One function for the replacement.** The route in `routers/pages.py` is
split: `pages.replace_attachment(ws, page_id, doc_id, original_filename, *,
actor, owner)` does the locked transaction, the annotation marks, the
highlight stamps and the relocation start, and returns the answer dict; the
route derives `actor` and `owner` from the request and calls it. The local
sink calls it with the link's `created_by` as both. Nothing else in the
route's behavior moves.

**The row.** `folder_links` gains four columns (schema version 46, the
pattern of step 36: add what is missing, re-runnable):

```text
kind     TEXT NOT NULL DEFAULT 'folder'   folder | file
page_id  TEXT NOT NULL DEFAULT ''         a file link's page
push     INTEGER NOT NULL DEFAULT 0       a folder link sends PDFs back; a file link always 1
state    TEXT NOT NULL DEFAULT '{}'       a file link's entry {doc, size, mtime, title}
```

A folder link's entries stay in its directory's `.gamma-sync.json`; a file
link has no directory of its own to own, so its one entry lives in the row.
`path` is the file for a file link, `UNIQUE` as before: one file feeds one
page. `clean_path` applies (outside the data directory, a full path) plus
"a file, `.pdf`". Limits per workspace are counted by kind: ten folders,
fifty files. The module keeps its name; a link is "a folder or a file on
disk" and the docs say so.

**Routes**, `/api/folder-links` as now, the desktop app the only caller:

- `POST {kind?: folder|file, folder?, page?, path, notes?, push?, remote_url?, token?, token_id?, workspace?}`.
  A file link checks the page exists (locally, or through the token's
  `GET /blocks/{id}`), and that the token is `write` when push is on.
- `PATCH {notes?, paused?, push?, token?, token_id?}`: turning push on for
  a remote folder link comes with the write token the shell minted; the
  server checks its scope on the remote and swaps it in.
- `POST /{id}/sync` runs push then pull; `GET` listings carry `kind`,
  `page_id`, `push` and, for a file link, its `title` from `state`.

**The loop.** `due()` gains one clause: a link with push is due when any
of its PDF entries has moved and settled, a stat per entry per tick. A
folder of a thousand papers is a thousand stats every thirty seconds, well
under a millisecond each, and the state file is re-read only when its own
mtime changed. A file link is one stat. Everything else about the tick,
the locks, the stale-run rule and the background threads stays.

**Tokens.** A push needs write scope. The shell mints `write` for a file
link on a remote server and for a folder link whose push is turned on, the
way the clone's token is minted, and revokes the old read token after the
swap. Write tokens are refused to viewers, which matches the editor role a
link already takes.

## The desktop app

Two ways in, both from where the person already is, both ending in a
native picker that is the person's say, as *Keep on this computer…* does.

**Follow a file.** The web app's Document menu, in the desktop app only,
gains *Follow a file on this computer…* under *Replace the PDF…*, with the
tooltip "Each time the file changes, it becomes this page's PDF; notes and
highlights stay". It posts `{source: "gamma-app", type: "follow-file-on-disk",
ws, page}` to its own origin; the page preload relays it as
`shell:follow-file-from-page` under the same guards as the folder message.
The shell's `followFile(wsId, pageId)`:

1. Reads the page's title for the notice and the row.
2. If the page already follows a file, shows a sheet: "*Draft on X* follows
   `D:\tex\draft-x\build\main.pdf`" with *Reveal*, *Stop following*, *Cancel*.
3. Else opens the file picker (`.pdf`; `GAMMA_SHELL_PICK_FILE` under the
   test harness), mints a write token on a remote, makes the link on the
   local server or the host, and says in the bar: "*Draft on X* follows
   `main.pdf`: each new version is sent to Gamma."
4. Offers background mode the first time, as a kept folder does.

**Send back from a kept folder.** The folder row in the sync panel gains a
fourth action, a toggle drawn as an upload arrow: *Send PDFs changed here
back to Gamma* / *Stop sending PDFs back*. On a remote folder the shell
mints the write token first. Nothing is asked at creation; the default is
off, because today's promise, "what you change on disk is never sent
back", is one people lean on when they put an annotated copy there.

**The panel.** `lib/keeping.js` turns rows into items; it gains a `file`
item and the panel a third group, *Files followed*, after the folders:

```text
Files followed
  Draft on X                              ●  ⏸ ⟳ ✕
  Sent · 14:02 · build\main.pdf
```

- `name` is the page title, `where` the file's path. A click reveals the
  file (`shell.showItemInFolder`).
- States, worst first as for a folder: *Problem* (the kept reason as the
  tooltip), *Syncing…*, *Waiting for the file to finish* (unsettled, shown
  as pending, never as an error), *Paused · 14:02*, *Not sent yet* (new),
  *Sent · 14:02* (ok). The tooltip of *Sent* adds what happened to the
  highlights when the status knows: "12 highlights placed, 2 to review".
- Actions: pause/resume (the tick leaves it alone), *sync* (send now, and
  the round's answer in the bar: "`main.pdf` sent to Gamma" or "nothing
  new to send"), *stop* (drop the link; the file is never touched).

A folder item with push on reads *Up to date · 14:02 · sends back*, its
counts add *n sent*, and *Sync*'s existing question about replacing changed
files is asked only for files that are *both changed*; a file that only the
disk changed is sent, not asked about. The button's summary counts
"2 folders and 1 file on disk". `summarize` keeps the same ranking, so the
button never reads worse than its worst row.

**In Gamma's page, nothing new.** The viewer switches to the new file as
after any replacement, the relocation job appears in Background tasks, and
dashed highlights get their review card. The job's title says where the
file came from ("Draft on X · from main.pdf"), so the task list tells a
build from a hand replacement.

## Policies, stated once

- **Direction.** A folder link pulls always and pushes PDFs when asked. A
  file link pushes always, pulls never. The notes file is one way.
- **Safety.** A round still writes, renames or removes only what it
  recorded, and never deletes in Gamma: a PDF removed from a kept folder is
  fetched again, a followed file removed pauses the link with a reason.
- **Both changed** keeps the disk file and reports; the person picks a
  winner with the two actions that exist.
- **Partial writes** are waited out, never stored.
- **Scope.** Only the desktop app's own local server keeps links, as now; a
  NAS or Cloud server only answers the uploads and the replacement, with a
  write token the person minted.
- **The page is not the file's name.** A push passes the page's current
  `original_filename` on a folder link (the kept name is a title, not a
  file name) and the file's own name on a file link when the page has none.

## Costs worth knowing

- **Storage from frequent builds.** Each replacement leaves the old file
  to the orphan purge, thirty days later, counting against the quota until
  then. Fifty builds a day of a 2 MB paper hold about 3 GB in flight on a
  busy week. A shorter life alone would be wrong: the review of a dashed or
  hollow highlight needs the file it was measured on for its Before picture,
  and a re-run reads that file for context; without it the card says "The
  previous version of this PDF is no longer stored" and shows the quote
  alone, and the re-run searches by quote without context. So two rules, as
  a second step:
  - *A pending highlight holds its file.* A dashed or hollow highlight
    names the file it was measured on (`pdf_position.doc_id`, and its
    `prev.doc_id`), and Accept, Use closest match and a move drop that
    name. Those names count as references in the purge's grammar
    (`storage.upload_refs`) while the highlight is `approx` or `lost`, so
    the file lives exactly as long as a review needs it and goes when the
    last pending highlight on it is resolved. This also mends the cliff a
    hand replacement has today, thirty days on. The hold is bounded: a
    chain of builds stamps a highlight once, so pending highlights hold the
    file they were made on, not every build in between. A clone receives a
    held file like any referenced one, so its card shows the picture too.
  - *A build nothing holds lives a day.* `upload_orphans` gets a `until`
    column, defaulting to `since + RETAIN_S`; a replacement made by a push
    sets the outgoing file's to a day, long after its relocation run has
    read it. The purge reads `until` instead of computing it. A file let go
    by the last Accept takes the regular thirty days from then, a corner
    that costs one file.

  Keeping a picture of the Before crop instead of the file was considered:
  smaller, but a re-run would still lose its context, and it puts a
  renderer in the job. It is the fallback if held files ever weigh.
- **Relocation runs.** One per push on a page with highlights; a 20-page
  manuscript with 30 highlights is seconds. The account's job cap applies;
  a push that cannot start the run reports `relocation_error` in its kept
  reason and the replacement stands, as for a hand replacement.
- **Bandwidth.** A push uploads only when the hash differs, never a touched
  file with the same bytes, and the server stores nothing twice.

## In steps

1. **Engine and server.** `Sink`, `Push`, the round's push-first order,
   `pages.replace_attachment`, the four columns and schema step 46, the
   routes' `kind`/`page`/`push`, `due()`'s disk clause, the client's
   `--push` and `push` command. Tests beside the existing ones in
   `test_folder_links.py` and `test_folder_sync.py`: a kept PDF changed on
   disk is sent and the next pull leaves it; same bytes touched sends
   nothing; both changed is kept with its reason; an unsettled file waits;
   a 409 is a kept entry; a file link on a local and on a remote server
   (the second TestClient, a write token); a read token is refused for
   push; the limits by kind.
2. **Desktop.** The menu item and message, `followFile`, the picker and
   `GAMMA_SHELL_PICK_FILE`, the file item and group in `keeping.js` with
   `keeping.test.js`, the folder row's toggle, the e2e step: follow a file,
   overwrite it, see the page's `doc_id` change and the row read *Sent*.
3. **Afterwards, each on its own.** A new PDF dropped in a kept directory
   becomes a page filed there (`POST /blocks/by-doc` with `folder`, the
   entry recorded by the push so the pull adopts it); embedded annotations
   imported after a folder push (`/import/pdf-annotations`, idempotent);
   the two
  retention rules above; a notes-only page whose stem matches a
   dropped PDF gets it attached.

## As built

Steps 1 and 2 were built, and the two retention rules of step 3 with
them rather than after. Where the build departs from the text above:

- **A followed file that is gone waits; it does not pause the link.** The
  loop's stat finds nothing moved, so the link is not run until the file
  is back; a sync asked for says "the file is not there" as a wait, not a
  problem. A build that deletes its output before writing it again would
  otherwise pause the link on every compile.
- **A file link's errors fail the link.** A 409, a refused token, a page
  gone or an account no longer an editor is the row's `last_error`, and
  the retry wait (`RETRY_S`) applies, after which the next round tries
  again. A folder link's push errors are kept entries, listed with the
  pull's, as designed.
- **`seen`.** An entry whose file a pass could not send (kept, changed
  in both places, or at rest without a PDF's end) records the file's stat
  as `seen`. The loop's disk clause and the client's `--watch` compare
  against it, so such a file does not run a round on every tick. `Push`
  itself compares with the entry, so a round that runs for another reason
  (a change in Gamma, a sync asked for, a file link's retry) looks at it
  again and sends it again when it is whole. A file still being written
  or locked records nothing, since a lock can end with no new stat, and a
  file that changed while it was sent (one that shrank under an upload
  among them, `gamma_sync.Changed`) waits for the next pass without
  `seen`.
- **The sink's `current(page_id)`** answers `{doc_id, filename, title}`,
  or None for a page that is gone, in the trash or no page, so a push
  picks the file name and a file link its title from the same read.
  `LocalSink` asks before each write whether the link's account is still
  an editor.
- **Retention is its own table.** Not an `until` column on
  `upload_orphans`: an additive `upload_retention (name, keep_s, since)`
  in each pages.db, written by `upload_gc.retain`. The replacement takes
  `keep_old_days` (1 to 30) and every push asks for 1
  (`gamma_sync.KEEP_OLD_DAYS`), folder or file, local or remote. A restore
  drops the rows, so restored files take the thirty days.
- **A file let go by the last Accept keeps its short life.** The outgoing
  file's retention row is written after the stamps, so it outlasts the
  highlights' hold: once the last pending highlight on it is resolved, a
  pushed build goes a day later, not thirty. A hand replacement's file
  takes the thirty days as before, and so does a pushed-out file another
  page still carries or shows (a duplicated page that shows it by its
  address): `retain` writes no row for a file a block shows, only for one
  that nothing or only highlights hold.
- **Held files stay home.** The hold is a reference for the orphan
  bookkeeping, not a file a page shows: a mirror (a page published to the
  share host among them) and an export through a share carry only what
  the pages show (`storage.upload_refs(..., held=False)`), since the share
  would refuse to serve the older build. The owner's own export and a
  backup carry it.
- **The hold counts every named file**, not only while the highlight is
  `approx` or `lost`: `pdf_position.doc_id` and `prev.doc_id` are
  references whatever the flags (`storage.upload_refs`). Only a pending or
  lost highlight carries them, since Accept, Use closest match, a move and
  a solid placement drop them, so the effect is the one designed.
  `unstored` names no file (`storage.UNSTORED`).
- **The client.** `push` also takes `--dry-run`, waits until the file was
  last written `SETTLE_S` ago before it looks, prints one line, and exits
  0 whether or not it sent, 1 for a refused or read-only token, a missing
  page or an unreachable server at the start. `init --push` checks for a
  write token. `sync --watch` also runs a round when a PDF moved on disk,
  not only when the change feed moved.
- **The job's title does not name the file.** The relocation run started
  by a push is titled after the page, as for a hand replacement; the task
  list does not tell a build from one.
- **The panel.** A followed file also reads *Up to date · 14:02* when its
  rounds found the file Gamma's already, and *Sent*'s tooltip does not
  count the highlights placed (the status keeps only `relocation_error`).
  The button's tooltip counts "2 folders on disk and 1 file followed".
  *Sync*'s question about files changed on disk asks only about the files
  a forced round replaces (a kept entry's `force`: changed on disk, or in
  both places). A PDF a send could not deliver is listed apart, as *n not
  synced*, since Gamma's file did not change and Replace would change
  nothing.
- **Paths.** A path inside the data directory is refused by identity too
  (device and file id), so a `\\?\` device path or an administrative
  share naming it is refused like its plain spelling.
- **Failures.** An answer that is no JSON (a sign-in or captive portal's
  page) is a `SyncError`, kept per file by a push; any other exception in
  a round ends it as failed (`unexpected error: …`) instead of leaving
  the link marked running. Turning push or notes on or off writes the row
  only; each round copies both into the state file, so a round running
  meanwhile cannot lose its files map to that write.
- **Tokens.** Turning sending back off keeps the write token; it goes
  with the link.
- **Mac App Store.** A followed file is kept as a security-scoped bookmark
  like a directory, checked for reading at launch. Whether a build tool
  that renames a new file over the followed one keeps that access is not
  checked on a Mac yet.

Still open from step 3: a new PDF dropped in a kept directory becoming a
page filed there, embedded annotations imported after a folder push, an
upload in parts for a file past a proxy's body limit, and a notes-only
page whose stem matches a dropped PDF getting it attached. The notes
file's way back stays in [folder-sync.md](folder-sync.md).
