# Long operations

The question: which of Gamma's operations can take minutes on a big
library, how does each one show that it is still working, and which
should become a background job with a progress bar? The trigger was
deleting a workspace, which showed nothing but a disabled button for as
long as it took. Surveyed in November 2026 from the code, with a few
in-process measurements (a throwaway data directory and the test suite's
TestClient, no network). The job system these findings build on is
[dev/tasks.md](../dev/tasks.md).

"Big" below means 5,000 pages, 50,000 blocks and 5 GB of PDFs. Estimates
that were not measured say so. Two limits set what "too long" means:

- **A proxy's cut.** Cloudflare ends a request that has sent no byte for
  100 s with a 524. The server does not notice: a sync handler keeps
  running in the thread pool and finishes the work, while the person sees
  an error for work that succeeded, and may try again.
- **The tab.** A loop that runs in the browser stops when the tab closes
  or reloads, part way, with nothing recorded.

## What was already right

Most long work was already a job ([dev/tasks.md](../dev/tasks.md)
"The kinds"): exports, workspace exports, snapshots, restores, the import
itself, batch metadata lookups, server snapshots, the indexer,
transcription and the highlight run. None of them has a progress gap:
`ws_backup.write_zip`, the restore, the export driver and the imports all
report bytes or pages.

Other subsystems keep their own thread and status rather than jobs, on
purpose, since they are recurring rounds rather than one-off tasks: a
mirror's fill (pages done of total, the file at hand), the Zotero link's
rounds (units and PDF bytes, bounded at 60 s or 50 units a round), the
off-site copies' round, the scheduled backups (which the jobs listing
already folds in as read-only rows). Uploads report their bytes and go in
32 MiB parts. The whole-document translation is a client queue with its
own progress whose finished chunks the server keeps. The AI chat streams
with keep-alive lines, so no proxy cuts it.

Measured and bounded, kept as they were:

| Operation | Cost on a big library |
|---|---|
| Delete a folder or a label (one `apply_batches` transaction) | 1,000 pages 0.07–0.21 s, 5,000 pages 0.36 s |
| Trash one page, restore one tree, list the trash | milliseconds; 1,000 trash entries list in 0.04 s |
| Delete a page permanently | one transaction; its files are only scheduled (`upload_gc`) and removed 30 days later off-request |
| Renames of folders, labels, pages, accounts | rewrite nothing: everything names by id |
| Quota, `storage.usage` | cached, keyed on the directory's mtime |

## What ran too long in one request

| Operation | Where | What makes it long | Estimate |
|---|---|---|---|
| **Delete a workspace** | `DELETE /api/workspaces/{ws}` | the final copy: `keep_final_copy` runs `write_zip` over the databases and every upload (DEFLATE over PDFs that are already compressed), then `rmtree` | 2–3 min on a desktop, 5–10 min on a NAS (estimated) |
| **Delete an account** (admin) | `DELETE /api/admin/users/{name}` | the same, once per workspace that goes with it, all zipped before anything is deleted | the sum of its workspaces |
| **Check databases** (admin) | `POST /api/admin/check-databases` | `PRAGMA quick_check` reads every page of `users.db` and every workspace's `pages.db` and `data.db` | seconds on a personal server, minutes with dozens of big workspaces (estimated) |
| **Download a server snapshot** (admin) | `GET /api/admin/backups/{name}/download` | the snapshot directory zipped into a temp file before the first byte | 1.5–4 min for a full 5 GB snapshot (estimated) |
| Preview of an import | `POST /api/import/review`, `…/parts/{token}/finish` | every PDF of a Zotero export inflated and hashed; a Gamma export copied and then unpacked whole | 30 s to several minutes (estimated) |
| Sync now | `POST /api/mirrors/{ws}/sync?wait=1`, the Zotero link's and a publication's | waits out a running round, then runs one; a first fill of 5,000 pages takes 25–80 min | unbounded |
| Empty Recently deleted | `DELETE /api/trash` | per entry: a subtree delete, two commits, and `pdf_index.purge_unused` scanning every paper | 300 entries with PDFs 2.5 s measured; 5,000 on a NAS disk past 100 s (extrapolated) |

The two deletes had more than a long wait wrong with them:

- A retry after the 524, or a second owner or an admin pressing Delete,
  passed every check (the rows go only after the zip) and wrote a second
  multi-gigabyte final copy beside the first, at the same time.
- Nothing said it was working: the confirm box closed and only the
  dialog's buttons went grey for the minutes the zip took. Behind a proxy
  the 524 read as a failure while the server went on and deleted the
  workspace, so the dialog stayed on a workspace about to vanish.

## What ran too long in the browser

Every bulk action of the library was a loop of one request per page, one
after the other: delete a selection, delete a folder with its pages, move
into a page or to the library, pin, add or remove a label or folder,
duplicate. The server's share is small (6.5–8 ms a page, measured over
1,000 pages), so the length is the round trips: 15–30 s for 1,000 pages
on a LAN, 80–200 s through a tunnel (estimated from the round trip). No
single request was slow, so no proxy cut them, but:

- Nothing showed while they ran: the confirm box closed and the only word
  came at the end.
- Errors were swallowed (`catch {}`), so "Moved 340 pages to Recently
  deleted" was said even when some failed.
- A tab closed part way left part of the selection done and the rest not,
  and a folder deleted "with its pages" stayed, half emptied, since the
  folder itself went only after the loop.

The real cost is the N round trips. A row of Background tasks that
counts, stops and reports failures honestly came first; then one request
for many pages: `POST /blocks/bulk` trashes a set of pages
(`ops.trash_pages`) or applies a set of properties patches
(`ops.apply_each`, `apply_batches` one batch at a time in a savepoint of
one transaction), each page checked and answered as its own request was,
and the app sends 100 pages a request (`pageBatch.runBulk`): 1,000 pages
are ten round trips, with the row and Stop between them. A duplicate
still goes page by page (each one reads its page whole and writes it
again).

## Found on the way

- **The orphan purge never caught up after a big Empty.**
  `upload_gc`'s safety cap (`PURGE_MAX = 100`, `PURGE_SHARE = 0.2`)
  refused the whole purge when more files fell due at once. Emptying a
  trash of 300 PDF pages records 300 orphans within seconds, so 30 days
  later they all fall due together, and every six-hourly reconcile
  refused them again (a log warning only). The files stayed stored and
  counted against the quota. Fixed below: the purge goes in capped chunks.
- **Settings → Maintenance's health table** sums the length of every
  indexed page's text (`pdf_index.doc_chars`, a full scan of `pdf_fts`) on
  each read, and the pane reads it again after every paper a metadata
  batch finishes. A count stored by the indexer would make it a lookup.
  Closed below (2026-11-08).
- **The Server pane's backup list** stats every file of every snapshot on
  each listing (`backups._dir_size`); the size could live in the
  snapshot's manifest. Closed below (2026-11-08).
- **Edits during a delete's final copy** were in neither the copy nor
  anywhere else once the rows went. Members other than the one deleting
  could keep editing for the minutes the zip takes. The web app learned it
  may only read from the session's role, not from the page socket, so a
  refusal mid-session would have surfaced as failed saves. Closed below
  (2026-11-08).

## Decided

Server work that can outrun a proxy becomes a job; client loops become a
counted row of Background tasks; the rest is listed as open.

- Workspace delete and account delete are jobs (`workspace-delete`,
  `account-delete`) with one path between them: the final copy reports its
  bytes per workspace, the job can be stopped until the copy is done (a
  stop deletes nothing and leaves no copy), and from the first deleted row
  on it can no longer be stopped. A workspace being deleted is claimed for
  the length of the delete, whoever starts it, so a second delete of it is
  refused instead of zipping it again.
- Check databases is a job (`db-check`) whose progress counts the files.
- A server snapshot's download streams the zip as it is made instead of
  zipping it into a temporary file first. A job was considered and
  declined: it would have kept a second full copy of the snapshot on the
  data disk for a day, counted against the admin's 10 GB of finished
  files, and turned a GET (which a hosted server's read-only mode lets
  through) into a POST. Streamed, the browser's own download shows the
  bytes from the first second.
- The library's page loops share one helper that shows a counting row with
  Stop from ten pages on and reports what failed. It does not ask before
  the tab closes: the app's own reloads (a workspace switch, a restore, a
  deleted workspace) would ask too, and the desktop app, which does not
  answer that question, would cancel them without a word.
- The synchronous endpoints stay for scripts, as the other kinds' twins do.

Later, the import's review became a job too (`import-preview`), which the
import dialog follows: started by the upload itself (`POST
/api/jobs/import-preview`, or the finish of `…/parts` for one past
32 MiB), it reads the review to the end however long it takes and keeps
it beside the staged upload, where the dialog reads it once the job is
done (`GET /api/import/review/{id}`). A dialog closed meanwhile leaves the
review to its job, whose row opens it again; Cancel stops it and drops the
upload. The Gamma preview no longer copies the upload or unpacks its
`uploads/`: it reads the staged zip in place and unpacks only the
databases and the `.ink` files (the one step that reads uploads, 41,
converts those). `POST /api/import/review` keeps answering in the request,
for scripts.

Reviewing the design against the code turned up a gap in the job registry
itself: the report that marks the point of no return set it before looking
at a stop, so a Stop pressed after the final copy's last report and before
the first deleted row was accepted ("Stopping…") and then ignored. The
report now raises for a stop that landed before it, under the same lock
`cancel` decides under; a restore had the same window.

Reviewing the built change found two more, now closed. Two co-owners of a
shared workspace whose accounts were deleted at once each listed what goes
while the other still owned it, so the second could take the workspace
along without a final copy: account deletes now pass their final copies
one at a time, and the second finds its list changed and stops. And a
delete that waited queued behind two other jobs ran even if whoever
started it had lost the right meanwhile (an admin demoted, an owner made
an editor): both delete kinds check again as they run.

That check still came before the final copy, so two admins deleting each
other at once could both pass it and both accounts go. Both kinds now
check once more at their point of no return, under the users.db write lock
with their first write, and an account delete takes the account's admin
flag in that same transaction: of the two, the second finds its starter no
admin and is refused. An account delete's second start, which a running
job's hold answered with a 400, now answers that job, as a workspace
delete's does.

Sync now no longer waits for its round. `POST /api/mirrors/{ws}/sync` and
the Zotero link's answer at once, the status marked `asked_at` until a
round starts and takes the mark off in the same write that sets
`running`; a folder link's (`/api/folder-links/{id}/sync`) takes the
round lock and writes `running` before it answers, as the off-site Copy
now does. Every Sync now (the pill, Settings' rows, the publish section,
the desktop app's folders and files) then reads the link until neither is
set and reports the round's outcome, its progress moving meanwhile. A
mark no round will answer (a detached link, a filter naming no page, a
restart, a step before the round that fails, whose error the status
then shows) is taken off, and a folder link reads `running` only while
its round holds the lock, however long that round takes, so nothing
follows for ever and nothing stops following a round still running. `?wait=1` stays, for
scripts and the tests ([dev/mirror.md](../dev/mirror.md#rounds-and-cadence)).

The orphan purge goes in capped chunks. The cap no longer refuses the
whole purge: once the pages.db checks pass (a root row, pages, `PRAGMA
quick_check`), a purge deletes the cap's worth (`upload_gc.purge_cap`: 100,
or a fifth of the workspace's files when that is fewer, never fewer than
10), the longest due first, and leaves the rest to the next six-hourly
pass with a warning that counts them. The 300 files of the Empty above, in
a workspace of 1,000, go in three passes; 5,000 go in fifty, under two
weeks. The cap still bounds what one pass takes from a pages.db that
reads wrong in a way the checks miss, which is why the chunks keep the
full pass's cadence instead of running back to back
([dev/user_db.md](../dev/user_db.md) "Stored files").

Empty Recently deleted, and the 30-day sweeper with it, is one pass on
one connection (2026-11-07): one read of the trash gives each entry with
the pages that went with it (`trash._trashed`), so no entry reads the
trash again, the `deleted_with` stamp is an indexed column for the reads
that remain (migration step 48), and `purge_unused` runs once after the
loop, as it already did. Each entry still commits on its own, so the
write lock is let go between pages and a restore meanwhile is not lost
([dev/home_library.md](../dev/home_library.md) "Recently deleted").

The off-site copies' rounds and a folder link's rounds report their
progress as the other rounds do. An off-site round keeps it in memory for
its status (`offsite._phase`): the databases looked at of all of them,
then the files it sends and their bytes, boto3's upload callback moving
the bytes within a file. To count the whole round from its start, the
uploads now compare every workspace's files with its listing before the
first is sent. Settings → Backups' status line reads "copying now… ·
files 120 / 3,400 · 1.2 GB / 8.0 GB", read every 3 s while the round runs
([dev/debugging.md](../dev/debugging.md#off-site-copies-in-a-bucket)). A
folder link's `Round` takes a `progress` callback in place of the `say`
the server threw away: the files fetched of those to fetch and the PDFs'
bytes by the manifest's `pdf_size`. `_folder_round` writes it into the
link's status at most once a second, and the desktop app's sync panel
reads *Syncing 30 / 120*
([dev/folder_sync.md](../dev/folder_sync.md) "A folder link's round").
Neither is a job, so neither is in the tasks tray: they stay recurring
rounds, shown where they are followed.

A share view's export is a job too (2026-11-08). The share host sits behind
Cloudflare, and a big shared folder's export ran in the one GET of
`/folders/{id}/export`, so the proxy's cut came before the zip did. The
share view now starts the same `export` job under its token (`POST
/api/jobs/export?share=`), held to the share as the downloads are. The
job's owner is the share (`share:<token>`), not the visitor, who may have
no account: the share counts as one account in the limits and the 10 GB
of files, link visitors start at most 30 in five minutes per IP, and the
job is reached by its id through that share alone, by any visitor the
share admits. No listing shows it, so visitors of one link do not see
each other's exports, and the share view follows only the jobs its tab
started. A run checks that the share still stands, and the sweep takes
the jobs of a share that is gone
([dev/tasks.md](../dev/tasks.md#a-share-views-exports)). The GET routes
stay, for scripts, the fixed bibliography link and another Gamma's import
by link, which fetches a whole shared folder as one Gamma export in one
request and can still be cut on a big one.

A workspace being deleted takes no more changes (2026-11-08). From the
moment its delete runs, the write gates (`require_ws(write=True)`, an edit
share's, a comment's, a restore or merge into it) refuse every change
with 423 `workspace_deleting` and the `X-Gamma-Workspace-Deleting`
header, and the session lists the workspace `deleting`. The web app
learns it from the first refused write: the fetch wrapper raises an
event, and the tab acts as a viewer of the workspace and says on a pill
that stays that changes are no longer saved; the refused edit is dropped
and the page refetched. A share view keeps that from page to page, and
the share's own answer says so to one opened meanwhile. Work that passed
its gate before the delete is stopped too: the delete stops the jobs that
change the workspace (imports, restores, metadata lookups, transcriptions,
highlight runs) before its final copy, failing them with the reason, and
an AI turn's changing tools are refused at each call. A stopped delete
lets the workspace go again. A request or an AI tool call already running
as the delete starts, and the background rounds (a clone's, a Zotero
link's), are not held
([dev/workspaces.md](../dev/workspaces.md) "Deleting a workspace").

## Still open

In the order they matter:

1. Another Gamma importing a big shared folder by its link: one GET of
   the folder's Gamma export, which a proxy can cut (above). The share
   job is there to use, but the importing tab would have to follow a job
   on the other server across origins.
2. Smaller: a folder link's round shows no progress while it sends PDFs
   back, nor within one large PDF it fetches.

Four smaller ones (2026-11-08). A publish that only changes the cloud
share's audience or role, on a page already published and there, runs no
round: it checks the mirror and its token and sets the share
([dev/mirror.md](../dev/mirror.md) "Publishing"). Unlinking Zotero takes
the link's marks off every page in one statement, in the transaction
that empties its tables, each page that lost one logged as a reload,
instead of one transaction per page ([dev/zotero_sync.md](../dev/zotero_sync.md)).
The health table reads each paper's count of characters from its
`pdf_fts_docs` row, which `store_doc` writes and an older index fills
once ([dev/user_db.md](../dev/user_db.md)), and the backup list reads each
snapshot's size from its manifest and the lines of the workspaces added
to it, measuring only a snapshot from before them
([dev/migrations.md](../dev/migrations.md)). Testing the snapshot lock
that `ws_backup.remove_all` takes turned up a snapshot that read its own
zip back after letting the lock go, so a delete waiting on it took the
zip first and the snapshot failed; it now answers under the lock.
