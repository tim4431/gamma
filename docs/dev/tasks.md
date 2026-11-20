# Background tasks

Long work runs in the background and shows in one place: the Background
tasks button in the topbar (on a phone, a row of the More sheet). Two kinds
of work meet there:

- **Server jobs**: exports, workspace exports, snapshots, restores, library
  imports and their reviews, batch metadata lookups, deletes of a workspace or an account,
  the admin's server snapshots and database checks, a scanned PDF's text
  recognized or forgotten, and the search indexer. A job lives
  on the server (`gamma/jobs.py`), so it goes on when the tab or the dialog
  that started it closes, and every tab and device of the account sees it.
- **This tab's own work**: a PDF loading in the viewer, a file uploading
  (its bytes and the time left at the rate kept so far, `taskKinds`
  `byteProgress`, patched once per visible percent), a folder or many files
  uploading as one batch (local kind `uploads`, whose click opens its
  dialog again; [import_export.md](import_export.md#uploading-a-folder-or-many-files)),
  an AI lookup (the open page's metadata, a title, a slide citation), a
  handwriting selection's Redraw as shapes or Convert to text (its Stop
  aborts the request and the handwriting stays as it was;
  [handwriting_ai.md](handwriting_ai.md)), a translation, a chat reply
  running under Unlimited rounds (its Stop stops the reply), a library
  action over many pages (below). These rows exist in this tab only;
  they stop with the tab.

The **Background tasks** tour says the part a spinner cannot — that the job
is the server's, so it outlives the dialog and the tab. It is offered on the
first `job.started`, which the store's `adopt` emits
([onboarding.md](onboarding.md)).

Code: `gamma/jobs.py` (the registry), `routers/jobs.py` (its API); each
kind's start endpoint in the router of its work; `frontend/src/tasks/` (the
store, the pure model, the kind registry, the tray).

## Jobs on the server (`gamma/jobs.py`)

A job is a row of `users.db`'s `jobs` table ([migrations.md](migrations.md)
step 24) and, while it is queued or running, a worker thread in this
process. The row holds the kind, the owner (the account that started it,
`""` for a workspace's own work, or `share:<token>` for a share view's
export, below), the workspace, an English title, the
parameters (JSON, what a window needs to show the job again), the state
(`queued`, `running`, `done`, `failed`, `cancelled`), the last progress, the
result (JSON), the error, and the file the job produced. A running job's
progress lives in memory and is laid over the row on every read, so a
progress report writes nothing. The final row is written before the job
leaves the live set, and a read takes the live job's state before it reads
the row (`_live_views`), so a read never sees a job ended without its result
or its file.

The endpoint of the work authorizes the request, then calls
`jobs.start(kind, owner=…, ws=…, title=…, params=…, run=fn)`. `fn(job)` runs
in the worker thread and returns the result. It reports through
`job.progress(done=, total=, unit=, phase=, item=)`:

- `unit` names what `done` and `total` count: `pages`, `bytes`, `files`,
  `items`, `papers`, `workspaces`, `segments`, `highlights`.
- `phase` is a short key the interface words (`packing`, `typesetting`,
  `unpacking`, `checking`, `saving`, `copying`, `restoring`, `annotations`,
  `databases`, `files`, the paper-handoff row's `browser` and `connector`,
  the transcription's `transcribing`, the highlight run's `reading` and
  `matching`, the text recognition's `reading`, `recognizing` and
  `updating`, a delete's `keeping` and `deleting`; `PHASES` in
  `tasks/taskKinds.js`).
- `item` is the thing at hand, a page title or a workspace name.
- Any other keyword goes into the progress as it is, a JSON number or
  string the kind's row reads: the text recognition's `since` (when its
  phase began, in ms) and `from` (its count then), from which the row
  tells the time left, and a run in a window's `idle` (the seconds no page
  came for, when that ended it).
- A new phase or unit starts its own counts; a field left out is kept.

Every report is also where a job stops: once the job was asked to stop,
`progress` raises `jobs.Cancelled`. It is a `BaseException`, so an import's
`except Exception` around one item never swallows it. `stoppable=False`
marks the point after which the job can no longer stop; a restore reports
it just before it swaps or merges the databases, a delete once its final
copy is written. That report is the last place to stop: a stop that landed
before it raises there (`cancel` and the report decide under one lock), so
a stop the interface took is never ignored. A job started with
`writes=True` changes its workspace (an import, a restore, a metadata
lookup, a transcription, a highlight run): a delete of that workspace
stops it before its final copy (`jobs.refuse_writers`), a queued one at
once and a running one at its next report, which raises `jobs.Refused` (a
`Cancelled` that fails the job with the reason), and waits for one past
its last stop. A job that writes a file
writes it to `job.artifact_path` and names the download with
`job.set_artifact(name, media_type)`. An exception fails the job: an
`HTTPException`'s detail, a `ValueError` or an `OSError` is the message as
it stands; anything else is logged with its traceback.

`jobs.start(ended=fn)` tells `fn(state)` how the job ended, once the job
reads as ended. It runs outside the jobs' lock, since it may start jobs,
and `jobs.wait` and `refuse_writers` wait for it. A queued job that
`cancel` stops is told too, before `cancel` returns. A queued job that
`refuse_writers` fails is not told. `state` is the row's (`done`,
`failed`, `cancelled`), except for two cases. A job that gave way is told
`"yielded"` (its row reads cancelled), and a running job that
`refuse_writers` stopped is told `"refused"` (its row reads failed). The
text recognition settles its file's automatic marker this way
([documents.md](documents.md#recognized-text)). A replacement's highlight
run lets its Zotero link go on ([zotero_sync.md](zotero_sync.md)).

The work itself takes the report as a plain `progress` callable, never
the job: `ws_backup.write_zip`, `create`, `restore_zip` and
`keep_final_copy`, `backups.create`, `workspaces.delete`, `delete_account`
and `keep_final_copies`, `integrity.check_all`, the export driver
`_run_export`, the import sources' previews and commits. A caller that does not watch passes nothing; the default is
`jobs.no_progress`.

**Limits.** An account runs two jobs at once and the server four; the rest
wait queued, oldest first. A workspace's own jobs (the indexer) run outside
those limits. A kind that keeps a core busy for hours runs in a lane of
its own instead (`KIND_LIMITS`, a function per kind read as the queue is
pumped): the text recognition, `ocr`, at most `GAMMA_OCR_WORKERS` (1 by
default) at once on the server, whoever starts them, the others queued,
and a queued one stops at once. A lane's jobs hold no place of the
server's four or an account's two, so a book's recognition never holds up
an export. A lane job started with `yields=True` (an automatic
recognition nobody asked for) starts after the lane's other queued jobs,
and gives way to one of them that waits. `_pump`, called wherever a
lane's jobs change, asks as many running yielding jobs of the lane to
give way as there are jobs waiting that do not yield. It withdraws a
give-way that nobody waits for any more (the job that waited was stopped,
or started) from a job that has not given way yet. A job asked to give
way raises `jobs.Yield` at its next report, decided under the lock, and
ends cancelled. Its `ended` hook is told `"yielded"` rather than
`"cancelled"`. Until it ends it reads `stopping: true`, and a start of its
work is not `Busy` with it but a job of its own. `jobs.promote(id)` makes
a job one that someone asked for, which no longer yields. It returns True
for a job promoted now or before, and False for one that is gone or that
gave way already. A kind whose work is done elsewhere (`AT_ONCE`:
`ocr-tab`, whose job waits for a window's pages) starts at once, outside
the lane and the running limits. An account may
have 20 queued or running jobs (429 past that), its lane and `AT_ONCE`
jobs among them. A `key` makes two jobs of one owner and kind the same work: while
one is queued or running, starting another raises `jobs.Busy` with it (the
indexer, one per workspace; an import and a review's reading, one each
per review; a highlight run, one
per page and file; a delete, one per workspace or account; the database
check, one per admin). Work done once whoever asks for it (a text
recognition, one per workspace and file) starts with `exclusive=True`:
`Busy` then comes for any owner's job of that kind and key, found in the
same turn of the lock as the job is added, so two starts at the same
moment get one run. `exclusive` may also be a tuple of kinds, for works
that must not run beside one another: the three that change a file's
recognized text pass `pdf_ocr.OCR_KINDS` (`ocr`, `ocr-tab`, `ocr-drop`),
and `Busy.job`'s `kind` says which was found. `jobs.live(kind, key)` reads
such a job of any owner, and `jobs.live_count(kind)` counts a kind's
queued and running jobs, whoever owns them.

**Files.** A job's file is `<data dir>/jobs/<id>/artifact`; an upload a job
reads (a restore's zip) waits in `jobs/incoming/` and the job removes it
once read. A zip past 32 MiB goes up in parts instead (`/jobs/restore/parts`,
the protocol of a PDF's upload in parts), since a proxy in front of the
server caps a request's body (Cloudflare at 100 MB). It is staged like an
import review's upload (`gamma/import_staging.py`, no size cap, refused only
when the disk would keep less than 1 GB free); the finish starts the job,
and the job removes the staged zip once it is over. A job that writes a file or keeps one does not start while the disk has less
than 1 GB free or the account's files take 10 GB: its finished jobs' files and
what all its listed jobs keep, a failed or stopped one's too (507, "download
them and remove them in Background tasks first"; `jobs._check_room`). Files are not metered
against the account's storage quota.

What a run needs that its params cannot hold is kept with the job:
`jobs.start(kept={name: bytes})` writes `jobs/<id>/kept/<name>` before the
row goes in, and `jobs.kept(id, name)` reads it back. It stays for as long
as the job is listed, however the job ended: a failure, a stop and
`recover` take only what the run wrote. Removing the job, clearing it and
the sweep take it with the row. It counts against the account's 10 GB like a
finished file, so jobs that fail at once cannot pile it up. A translated annotated-PDF export keeps
the viewer's layout this way, so it can be started again
([translation.md](translation.md#exporting-the-translation)).

**Ending.** A finished job is kept 24 hours (`RETENTION_S`). The sweep
(`jobs.sweep`, every 10 minutes from the app lifespan) removes it with its
file and what it kept, the jobs of a share that no longer exists, job directories no row names and uploads no job took, then starts the automatic text recognitions left waiting (`ocr_auto.resume`). At startup
`jobs.recover` fails the jobs a stopped process left queued or running
("interrupted: the server restarted while this ran"). A process only
recovers rows another process stamped (`instance`), so a second app
lifespan in the same process never fails its own jobs. The jobs run in the
one server process, like the page sockets. A job's owner is the account's
id, so a rename leaves its jobs as they are; a deleted account's jobs go
with it (`workspaces.delete_account`).

## The API (`routers/jobs.py`)

Only a session account uses jobs (integration tokens get 403, like every
account-level endpoint), and a share view for its exports (below). The one
exception for a token: a token's `PUT
/pages/{id}/attachment` that replaces a page's PDF starts its account's
`relocate-highlights` run, as a session's does, and the account's sessions
see it in Background tasks while the token cannot read it. A job is its
starter's business: another account
gets 404 for it. A workspace's own jobs are seen by the workspace's members
while they work in it (the request's workspace), and stopped or removed by
its editors and owners. Endpoints and shapes: [api.md](api.md) "Background
jobs".

- `GET /api/jobs` lists the account's newest 100 jobs without their
  results, plus the request workspace's own. Two subsystems that keep their
  own state add read-only rows to that listing rather than becoming jobs:
  scheduled backups (`gamma/backup_schedule.py`) as `scheduled-backup`
  while one is queued or running — a finished or failed run stays in
  Settings → Backups, and a failure raises the `backup-failed` notice —
  and the AI chat's blocked paper fetches (`gamma/fetch_handoff.py`) as
  `paper-handoff` while one waits for the user's browser, so a card whose
  reply has scrolled away is still somewhere to find. A click on that row
  opens the publisher's page; the chat's own card settles the request
  ([ai_tools.md](ai_tools.md#walls-and-the-browser-handoff)). A third,
  `computer-turn`, is a chat turn of an agent on this computer queued or
  running for the account, or for a co-author its agent serves
  (`params.person`; [local_agents.md](local_agents.md) "Its model, its
  typing, a terminal…"): a click opens the page's chat on the computer side
  on the turn's conversation (`taskKinds.turnShown`: a comment thread's, a
  co-author's, else the page's own; a turn of another workspace, only its
  page), and its Stop goes to
  the turn's own stop route, not `cancelJob`. While one asks the person
  (`progress.phase` `asking`, taskModel `waitsForYou`) its row has a still
  shield and no bar, and the tray button the accent dot instead of the
  spinner.
- `GET /api/jobs/{id}` is one job with its `result`.
- `POST /api/jobs/{id}/cancel`, `DELETE /api/jobs/{id}` and
  `POST /api/jobs/clear` stop, remove and clear jobs.
- `GET /api/jobs/{id}/download` streams the file. A plain link works, so
  the browser's download manager fetches it however big it is. The first
  download stamps `downloaded_at`.

## The kinds

Each kind starts at `POST /api/jobs/<route>`, in the router of its work.

| Kind | Route | Started from | Result, file |
|---|---|---|---|
| `export` | `export` (`routers/export.py`) | the Export dialog, for a page or a folder, a share view's too | the file; `{pages, skipped}` |
| `import-preview` | `import-preview` (`routers/imports.py`, an uploaded file; `import-preview/parts` for one in parts; one per review) | the import dialog, once the file is chosen | `{review_id, pages, warnings}`, the counts; the review itself is kept with the staged upload (`GET /import/review/{id}`) |
| `import` | `import` (`routers/imports.py`) | the import review's Import to library | the import report |
| `workspace-export` | `workspace-export` (`routers/ws_backups.py`) | Settings → Workspaces → Data → Export…, Export all | the zip |
| `workspace-copy` | `workspace-copy` (`routers/ws_backups.py`, `gamma/workspace_copy.py`) | the library's page menu → Copy to workspace ▸; Settings → Workspaces → Data → Duplicate… (a clone's or a Zotero link's More) | the merge's counts with `workspace`, the target, and `zotero_pdfs`; the job's `ws` is the target (empty for a duplicate, whose workspace is made as it runs), and the tab that started it says what came and offers Open ([workspaces.md](workspaces.md) "Copies between workspaces") |
| `snapshot` | `snapshot` | Settings → Backups → Back up now, Back up all | `{snapshots, failed}` |
| `restore` | `restore` (an uploaded zip), `restore-snapshot`, `bucket-restore` (`params.source` `bucket` or `drive`; `into: "new"` makes the workspace) | Settings → Workspaces → Data → Merge / Restore, Settings → Backups → Restore (Restore as new… for a copy in the bucket or in Google Drive), a shared page or folder imported by link | the restore report |
| `bucket-download` | `bucket-download` (`routers/ws_backups.py`, admins; `params.store` `bucket` or `drive`) | Settings → Backups → a copy only the bucket or Google Drive holds → Download | the zip |
| `metadata` | `metadata` (`routers/metadata.py`) | Settings → Maintenance → Fetch needed / Fetch selected / Refetch; an upload of several PDFs | `{papers, fetched, failed}` |
| `server-backup` | `server-backup` (`routers/admin.py`) | Settings → Server → Server backups | the snapshot's info |
| `indexing` | — (`gamma/search/indexer.py`, owner `""`) | a search, a page chat, Settings → Maintenance → Rebuild | `{documents}`; progress in documents, written in batches, a stopped run storing what it read |
| `transcribe` | `transcribe` (`routers/recordings.py`, one per recording block) | a recording's menu → Transcribe; every recording that ends, with Settings → Reading & editing › Recordings › Transcribe recordings on | `{paragraphs, model}` ([recording.md](recording.md)) |
| `workspace-delete` | `workspace-delete` (`routers/workspaces.py`, one per workspace) | Settings → Workspaces → Manage → Delete…, for an owner or an admin | `{warning, final_copy}`; phases `keeping` (the final copy's bytes) and `deleting`, past which it no longer stops ([workspaces.md](workspaces.md) "Deleting a workspace") |
| `account-delete` | `account-delete` (`routers/admin.py`, one per account; `workspaces.start_account_delete`) | Settings → Users → an account → Delete… (admins); on a share host also the server's own, owner `jobs.WORKSPACE`, for an account whose Gamma Cloud account was deleted ([cloud_accounts.md](cloud_accounts.md) "A deleted account on the share host") | `{deleted_workspaces}`; the same phases, a final copy per workspace that goes |
| `db-check` | `db-check` (`routers/admin.py`) | Settings → Server → Databases → Check now (admins) | what `/admin/check-databases` answers; progress in files |
| `ocr` | `ocr` (`routers/ocr.py`, one OCR job per workspace and file, whoever starts it; its own lane) | the Document menu → Recognize text in this PDF, where the server recognizes text itself; a scan uploaded with no text, by itself (owner `jobs.WORKSPACE`, `params.auto`, `yields`; `gamma/ocr_auto.py`) | `{scanned, recognized, kept, empty, failed, good_layer, shared, highlights_respelled, highlights_kept, highlights_changed}`: the scan and picture pages, those reading recognized words, keeping their own text and recognized without words (the whole file's), this run's pages without a result, whether the file's own text reads well, the median share of a layer's words the recognition read too (0 to 1, null with no layer), what the quotes' respell did; params `{page_id, doc_id, title}`, plus `auto: true` for an automatic one; phases `reading`, `recognizing` and `updating`, past which it no longer stops ([documents.md](documents.md#recognized-text)) |
| `ocr-tab` | `ocr` with `window` (`routers/ocr.py`; `AT_ONCE`, outside the lane) | the same menu row, where the window reads the pages (`/api/server-config`'s `ocr.where` is `window`) | the `ocr` result, `failed` the pages the window found no result on; params `{page_id, doc_id, title, window: {doc_id, engine, langs, ver, client}}` (`client` the window that started it, kept after another window takes the run over); the same phases; ends cancelled with `progress.idle` when no page came for 10 minutes of its `recognizing` phase, and without `idle` when stopped ([documents.md](documents.md#recognized-text)) |
| `ocr-drop` | `ocr-drop` (`routers/ocr.py`, outside the lane) | the Document menu → Forget the recognized text… | `{pages, highlights_respelled, highlights_kept, highlights_changed, highlights_unread}`: the pages that read recognized text before, and what the respell back did; params `{page_id, doc_id, title}`; phase `updating` from its start, so it never stops; no retry |
| `relocate-highlights` | `relocate-highlights` (`routers/pages.py`, one per page and file; `PUT /pages/{id}/attachment` starts it by itself) | a replaced PDF (the Document menu → Replace the PDF…, a build script's `PUT`, a Zotero link bringing Zotero's new file, which starts it for the link's owner, [zotero_sync.md](zotero_sync.md#pdfs)); a replaced text file over 512 KiB (a smaller one's ranges are placed in the replacement itself, no task, [highlight_relocation.md](highlight_relocation.md#text-files)); the Document menu → Find highlights in this PDF (a text file's: Find highlights in this file) | `{highlights, moved, kept, lost, approx, changed, min_match, exact_chars}` (a text file's with `format: "text"`): the counts, then the owner's thresholds the run went by, which its params `{page_id, title, doc_id, format, min_match, exact_chars}` record as it starts, `format` naming which run places it, `pdf` or `text` ([highlight_relocation.md](highlight_relocation.md#the-thresholds)) |

The off-site copies' Copy now (Settings → Backups, `POST
/api/admin/offsite/run`) is not a job: it starts the server's own
scheduled round early, one at a time, and the section's status line
follows it, its progress included (`gamma/offsite.py`). Nor is Sync now
of a clone, a publication, a Zotero link or a synced vault: it answers at
once and the surface that asked follows the round's own status to its end
(`asked_at` until the round starts, then `running` with its `progress`),
so no request waits out a long round ([mirror.md](mirror.md#rounds-and-cadence),
[synced_vaults.md](synced_vaults.md#when-rounds-run)).

A metadata batch looks its papers up one at a time; a paper nothing is found
for counts as failed and the batch goes on. A stop lands between two
papers, so the lookup in progress finishes first. An account has one batch
per workspace: a start while it is queued or running adds its papers to it
and answers that job (a paper still waiting is not added twice), so
Maintenance and uploads share one of the account's two job slots. Because
the batch can grow, its tray title counts its progress's total. A stopping
batch takes no more papers; the next start begins a new one. When it ends,
the tab that started it says how many were fetched, and the open
workspace's library reloads, since titles may have changed.

The export driver reads a folder's pages again when the job runs, so a
queued export holds what the folder holds then.

An import goes through two jobs. The review's (`import-preview`) stages
the upload as it starts (`gamma/import_staging.py`, in parts past 32 MiB
like a restore's zip) and runs the source's preview over it, reporting
its items (a Gamma export's `unpacking` and `checking`), so the largest
archives, which a proxy would cut in one request, are read to the end.
It keeps the review beside the upload rather than in its result, which
stays small. A stop or a failure drops the upload; once the preview is
read the job can no longer stop. A review the server's restart cut short
(failed by `recover`) leaves it: the dialog that still shows the review
drops it on its Retry preview or its close, else the staging's sweep does
two hours on. Discarding the review
(`DELETE /import/review/{id}`, the dialog's Cancel) stops a running one,
and removes a finished one's row. An import is refused (409) while its
review is still read, and once it starts its row stands for the review,
whose row goes. The import job (`import`) holds the staged review's claim
while it reads the upload and removes the upload when it ends, however it
ends. Asking again for the same review answers the job already started,
for either kind (an import 409 with another selection). The indexer keeps one row per
workspace (`jobs.prune`). Synchronous endpoints serve scripts, another
Gamma's import by link and the MCP export: `/api/export`, `/export-all`, `/import-data`,
`/import/review` (the review answered in the request),
`/pages/{id}/export`, `/folders/{id}/export`, the snapshot endpoints,
`DELETE /workspaces/{id}`, `DELETE /admin/users/{name}` and
`/admin/check-databases`. They run the same code as the jobs.

A delete holds its workspaces while it runs, whoever started it, so a
second delete of one is refused rather than zipped again; its starter
asking for the same workspace's or account's delete again is answered
with the job, which the web app then follows. Who may delete is checked
as a delete starts, again as it runs and once more at its point of no
return, under the users.db write lock with the first write, so a
demotion or another delete that came first wins. While a delete runs, its
workspaces take no more changes (423 `workspace_deleting`), and before
its final copy it stops their `writes` jobs, which fail saying the
workspace is being deleted ([workspaces.md](workspaces.md) "Deleting a workspace"). Neither delete kind has a
`retry`: starting one again goes through its confirmation. A server
snapshot's download (`GET /admin/backups/{name}/download`) is no job: it
streams the zip as it is made, so the browser's download starts at once and
shows its bytes.

A highlight run (`relocate-highlights`,
[highlight_relocation.md](highlight_relocation.md)) is keyed by the page
and the file it places for (`<page id>:<doc_id>`). A start while one runs
for that page and file answers it (409 only while it is still stopping). A
replacement first stops the same account's run for the file it replaced
and starts the new file's, which never waits for the old one. A stop lands
within a page of the search; once the matching is done the run marks
itself unstoppable and writes. When the replacement's run cannot start
(the account's jobs are full, the last run for that file is still
stopping, or the new PDF is shown from a URL alone and not stored), the
replacement stands and its answer says why (`relocation_error`).

A text recognition (`ocr`, `ocr-tab`) and a forget (`ocr-drop`) are keyed
by the workspace and the file (`<ws>:<doc_id>`) and start with
`exclusive=pdf_ocr.OCR_KINDS`: one of the three at a time for a file,
whoever starts it. A start beside one is answered by `busy_job`: the same
account's job of the kind asked for answers it (409 while it is still
stopping); a forget going gets 409 "This PDF's recognized text is being
forgotten — try again in a moment."; another account's recognition gets
409 "Someone else is recognizing this PDF's text right now."; the
account's own of another kind, or an automatic one, gets 409 "This PDF's
text is being recognized — wait for it, or stop it in Background tasks."
A person's `ocr` start beside the workspace's automatic `ocr` run of the
same file gets that job instead, promoted so it no longer yields. If that
run gave way already, the start is a job of its own, queued behind the
run that took the lane, never the job that is ending. Before
that the route refuses a guest (403) and a viewer and needs a PDF stored
on the server (400). A server run sets the engine up once
(`ocr_engine.check`, 503 with its sentence when it cannot). The jobs
write, so a delete of the workspace stops them. A stop lands between two
pages, and the pages read so far wait, unseen, for the next run, which
goes on from there. The `recognizing` phase counts the pages done by
earlier runs too and carries `since` and `from`. A failure in the
finishing steps after the text went live is logged and the job still ends
done, the file left pending for the next run to finish
([documents.md](documents.md#recognized-text)).

An automatic run (`gamma/ocr_auto.py`) is the workspace's own `ocr` job,
which its members see and its editors stop. At most one goes on the
server, started only while no `ocr` job is queued or running, and it
yields to a person's run waiting for the lane. Every `ocr` job's `ended`
hook (`ocr_auto.ended`) settles the file's marker and starts the next
waiting file. A run that gave way, or that a delete of its workspace
stopped (`refused`), stays queued. A run that was stopped, running or
still waiting for the lane, is declined. A run that is done or failed is
done. Before restarting a file after a restart of the server, it
dismisses the interrupted row the restart left.

A run in a window (`ocr-tab`) holds no lane place, since the window's CPU
does the reading. Its job waits for the window's pages and applies them
(`pdf_ocr.window_run`). A Stop by its owner, a person's or the window's
own after a failure on its side, ends the job at its next report, within
2 s. The job ends cancelled without `progress.idle`, which only its idle
end sets, and its staged pages are kept. Until then it reads `running`
with `stopping: true`, and a page sent meanwhile or after lands nowhere
(409).

## The web app (`frontend/src/tasks/`)

`useTasks.js` is the store, one instance in App.jsx:

- It polls `GET /api/jobs` every 1.5 s while a job runs or waits, or while
  something watches (the tray open, a dialog on a job: `watch()`). Otherwise
  it polls every 30 s, and never while the tab is hidden. An older answer
  never replaces a newer one, and a listing that reads like the last one
  changes no state (the idle poll would otherwise re-render the app every
  half minute).
- `start(route, body, meta)` and `upload(route, form, {name, meta})` start
  a job, known at once, before the next listing. An upload is a row of its
  own until the server answers with the job. `meta.download` is `auto`
  (fetch the file once it is ready), `offer` or `none`.
- When a job ends, the store fetches its file if asked, then calls
  `onFinished(job, started)` once per job. That includes a job that ended
  before any listing saw it running.
- `expectEnd(match)` is for an end the tab heard of elsewhere. App's
  `onOcr` calls it with `ocrState.fileJobs(doc_id)` when a page's socket
  says that a file's recognized text changed. The store lists at once.
  For 15 s, a listing that first shows a matching job already ended (one
  the listing before did not have) settles it as if it had been seen
  running (`newlyFinished`'s `expected`). So the server's own run of an
  upload, done between two slow listings, is still reported on the page.
- This tab's rows: `addLocal`, `updateLocal`, `reviveLocal`, `removeLocal`,
  and `cancel` to stop one. A late report of work the person stopped never
  overwrites "cancelled". A run that waits its turn to read a scan in this
  window has such a row (kind `ocr-tab`, "Waiting for its turn…",
  `pdf/ocrRuns.js`). Its Stop calls the run off, and the job's row takes
  over at the run's turn. For a job, `cancel` first calls its kind's
  `halt(task)`, which ends this tab's own side of the work at once. An
  `ocr-tab` row's halt ends this window's run of the job
  (`ocrRuns.jobStopped`), so the run does not wait for its next request.

`taskModel.js` and `taskKinds.js` are pure, tested in
`tests/tasks.test.mjs`, except for what `ocr-tab` reads of this window:
its hidden time and its run here. `taskModel.js` holds the states, the
order (what runs first, oldest on top; then what ended, newest on top) and
the tray badge. `taskKinds.js` gives each kind its icon, title, detail line and row
actions, how long a kind that can tell still takes (`left(task, now)`,
after the counts: the `ocr` row's, at the pace of this run's pages since
`since`; the `ocr-tab` row's in the window reading it leaves out the time
that window was hidden, `noteHidden`), where a click opens it (`open`) and
how to start it again (`retry`: the same route and body; an `ocr-tab` row
is read again in this window, or on the server where the server now reads
text itself, `ocrState.recognizeAgain`; an export that carried the viewer's
translation, or a folder's that carried its papers', names its job as
`translation_of`, since its params cannot hold the layout and the server
keeps it with the job,
[translation.md](translation.md#exporting-the-translation)).

`TasksTray.jsx` holds the button, the panel and its rows:

- The button shows a spinner while anything runs, a red dot for a failure
  worth a look, and an accent dot for a file waiting to be downloaded.
- The panel lists In progress, then Finished, with Clear finished, which
  takes every finished row away at once, the server's before it answers.
  Its list moves as it changes ([ui-design.md](ui-design.md) "Lists that
  change"): a task that finishes glides past the header to the top of
  Finished, a new one fades in, and one removed leaves from where it stood
  while the rest close up.
- A row has its title, elapsed or finish time, bar and detail, plus Stop,
  Download, Start again and Remove. A click on an export or import row
  opens its dialog on that job again; a backup row opens Settings → Backups.
- `TaskProgress` is the progress block the dialogs show; for a row of this
  tab's (the upload dialog's batch) it shows the row's own line and how
  long it has run. `TaskBlock` is
  the one beside what a job works on, in Settings: its title, that
  progress block and Stop while the job can still stop. The Backups pane
  shows a workspace's snapshot, restore or delete with it, and holds the
  workspace's Back up and Restore meanwhile. Server → Server backups
  shows a server snapshot with it. Settings → Workspaces, Users and
  Server show a workspace or an account being deleted under its row,
  which offers nothing else meanwhile. Server → Databases shows its
  check, and the row keeps the newest check's findings, also after a
  reopen.
- `useActiveJobs(tasks, kind, param)` and `useJobEnd(tasks, kinds, onEnd)`
  (in `useTasks.js`) are how a pane follows jobs: the first maps what each
  waiting or running job of a kind works on (its `param`, a delete's `ws`)
  to the job, the second hears that a job of those kinds ended.
  `useJobEnd` fires wherever the job was started, for a job the pane saw
  waiting or running, or one this tab started that the server answered as
  over already, so the pane reads again what it changed.

`useAppJobs.js` is App's side of the jobs, one instance over that store:
starting what the menus and dialogs ask for (`startWorkspaceExport`,
`runBackupImport`, the export dialog's `runExport` / `closeExport`),
opening a tray row again (`openTask`) and retrying one (`retryTask`). Its `onJobFinished`, which the store calls, says what a
window does not show:

- A file a closed dialog left behind is offered in the pill with a
  Download button.
- A restore into the open workspace reloads the tab that started it, and
  offers a Reload to any other tab.
- An import into the open workspace refreshes the library. One that
  brought one page (`importReview.onlyPage`) and ended with its dialog
  closed offers that page in the pill, whose Open opens it.
- A review read while its dialog was closed is offered in the pill, whose
  Open shows it; a failed one says why.
- A database check started here says whether every file passed.
- A workspace or an account deleted: when the open workspace went with
  it, the tab reloads into the account's default workspace (no `?ws`)
  without reading the session first, which would show the "workspace
  unavailable" page for a moment; otherwise the session is read again, so
  the workspace switcher drops what went. The tab that started it says how
  it ended (the final copy kept, the warning of files left behind, a
  failure or a stop).
- A highlight run says what it found in the tab that started it
  (`relocationSummary`): how many highlights moved, were matched roughly or
  estimated (drawn dashed, with where to compare and accept them), stayed,
  were not found, and were left as someone moved them meanwhile. A run stopped or refused because the PDF was replaced
  again says nothing (`relocationSuperseded`): the newer run reports. The
  positions themselves reach every tab over the page socket.
- A text recognition or a forget, however it ended, makes every tab of the
  workspace that heard it end read the file's recognition again
  (`ocrState.refresh`, [pdf_loading.md](pdf_loading.md#recognized-words)).
  What a tab says is `taskKinds.ocrEnding`'s. The tab that started a
  recognition says how it went (`ocrSummary`): the pages recognized and the
  highlights that took the new text, the pages that could not be read, the
  file's own text kept because it reads well (and its picture pages
  recognized, when there were some), no scanned pages, or where a stop left
  it: how many pages were done when it stopped while recognizing, and only
  "was stopped" during the other phases, since the reading phase counts
  pages classified, not recognized. A run in a window says nothing of a
  job that it goes on from while this window still reads. That is a job
  failed by a restart of its server, or one the idle timer ended
  (`progress.idle`) while the window was hidden, which the run starts
  again. It also says nothing of a job whose end it reported already
  (`told`): the run failed on this window's side, said why on the status
  line and stopped the job. A job that applied after all is still
  reported, and so is a person's Stop, which carries no `idle`. A forget
  says how many pages read their own text again and what became of the
  highlights there (`ocrDropSummary`). A done automatic run is told on the
  page it recognized, in any tab of the workspace showing it: "Text
  recognized automatically on {n} pages of “{title}”." (`ocrAutoSummary`),
  even when no listing saw it run (`expectEnd`, above).
- A job started from a menu shows its progress in the pill while it runs
  (`meta.pill`).

The Export dialog's last step follows the export job
([import_export.md](import_export.md) "The Export dialog"). It shows
progress and Stop while the job runs. Once the file is ready, the dialog
downloads it, lists the pages left out and says what to do next. It may
close any time, and the job's row opens it again. The import dialog
follows its two jobs the same way: the review's while the server reads the
upload, then the import's. While the file goes up it is the dialog's own
and has no row, so a close by the backdrop, Escape or × asks first; a
close while the review is read asks nothing and leaves it to its job,
whose row opens the dialog on the review. A review on screen asks first
too, since a close drops it.

An upload of a folder or of several files is one batch in the tab
(`transfers/uploadBatch.js`), one row however many files, "12 of 340 files
· 120 MB of 1.2 GB · about 3 minutes left": three files on their way at a
time, Stop aborting them and starting no more. Its dialog asks first when
it is large and follows it to its end; the details are in
[import_export.md](import_export.md#uploading-a-folder-or-many-files).

The library's actions over many pages (delete, delete a folder with its
pages, move into a page or to the library, pin, add or remove a label or
a folder) go to the server 100 pages a request (`POST /blocks/bulk`,
[api.md](api.md); `library/pageBatch.js` `runBulk`), which trashes or
patches them in one transaction and answers each page's outcome. A
duplicate has no bulk form and sends a request a page, one at a time
(`runBatch`). From ten pages on either is a row of Background tasks
(local kind `batch`), "12 of 340 pages", patched once per visible percent
(at most once a request), and its Stop takes no new request. The pages a
delete takes leave the library at once and stay out of the listings read
meanwhile; the read after the batch brings back any that stayed. The
closing status says what went, where a Stop left it and what failed with
the first error (`batchText`), counted in the batch's own pages. A folder
deleted with its pages goes only once every one of them went, without a
Stop. The batch lives in the tab: a reload or a closed tab ends it part
way. It does not ask before the tab goes, since the app's own reloads (a
workspace switch, a restore, a deleted workspace) would ask too, and the
desktop app would cancel them without a word.

## A share view's exports

A share view's Export runs the same `export` job, started under the
share token (`POST /api/jobs/export?share=`), so a big shared folder's
export is not cut by a proxy in front of the share host. The job is the
share's: its owner is `share:<token>` (`jobs.share_owner`,
`auth.share_job_owner`), the same for every visitor, signed in or not, so
the share counts as one account in the limits (two running, twenty
waiting or running, 10 GB of files). A link visitor starts at most 30 in
five minutes per IP. The export reaches only what the share does (403
outside it) and is held to it as the share's downloads are
([import_export.md](import_export.md#gamma-to-gamma-export)); it takes no
translation. As it runs it checks again that the share stands ("the share
link was stopped") and still reaches the page or folder.

No listing shows such a job. Whoever holds its id reads, stops, removes and
downloads it through `/api/jobs/{id}…?share=<token>`, any visitor the
share admits, and nobody does without that share: the workspace's own
members get 404 for it, and a share stopped or changed to keep the
visitor out answers 401 or 403. The sweep stops and removes the jobs of a
share that is gone.

The share view's store follows only the share's jobs its tab started
(`useTasks({follow: true})`: `GET /api/jobs/{id}` for each one waiting or
running whose owner is `share:<token>`, taskModel's `followed`, never the
listing), and it has no tray. A job of the visitor's own account that the
share view adopted (a recording's transcript) is not followed: the share
does not reach it. A read the share refuses (401: stopped or keeping the
visitor out; 403) or that finds no job (404) ends the job there as failed
with why (`afterFollow`), so the dialog says so and the tab stops asking;
any other failed read (offline, a 5xx) is asked again next round. The Export dialog
follows the job as in the library; closed meanwhile, its file is offered
in the pill once it is ready. A reload loses the job to the tab: the share
view keeps no row to open it again.

Its "Add to my library" hands the link to the visitor's own library,
which imports it ([import_export.md](import_export.md) "Importing a shared
page or folder by link").

## Adding a kind

1. Write the work as a function of the domain module that takes a
   `progress` callable, and report each unit of work through it.
2. Add `POST /api/jobs/<route>` to the router that owns the work. It
   authorizes the request, checks what can be checked before starting, and
   calls `jobs.start` with the parameters a window needs, `artifact=True`
   when the job writes a file, and a `key` when two starts of the same work
   must not run at once.
3. Add the kind to `TASK_KINDS` in `taskKinds.js`: icon, title from the
   params, and `open` and `retry` where they apply. Then add its strings to
   the catalogs ([i18n.md](i18n.md)).
4. Test the job through the API (`tests/test_jobs.py` shows the pattern:
   `jobs.wait(id)` blocks until it ends).

## Tests

- Backend: `test_jobs.py` covers the registry and the API: lifecycle,
  progress, stop, failure, limits, visibility, files, sweep, recovery and
  the account lifecycle. `test_export_jobs.py` covers every export format
  as a job and the annotated-PDF folder zip, `test_share_export.py` a
  share view's (every format held to the share, the job the share's own,
  checked as it runs and gone with the share); `test_backup_jobs.py` the
  snapshots, restores and server backups. The import job and the
  review's (kept for its dialog, in parts, stopped, refused, a Gamma
  export unpacking no PDF) are tested in `test_import_review.py`, the metadata batch in `test_metadata_batch.py`
  and the indexer in `test_search.py`. `test_jobs_lanes.py` covers the
  lane (one recognition running and one queued while an export starts,
  `GAMMA_OCR_WORKERS`, a queued one stopped and its end hook told),
  `jobs.live`, the exclusive start with a kind or a tuple of kinds,
  `AT_ONCE`, a yield, a give-way withdrawn, `promote` and a job that gave
  way, and the extra progress fields; `test_ocr_api.py` the recognition's
  start, a second member's start between a look and a start included;
  `test_ocr_window.py` the `ocr-tab` job (its idle time, a stop by its
  owner, a take-over, a start beside an automatic run that gave way),
  `test_ocr_auto.py` the automatic runs and their queue (a run stopped
  while it waits, one a delete stopped), `test_ocr_forget.py` the
  `ocr-drop` job and Busy across the three kinds. `test_delete_jobs.py` covers the
  delete kinds (a stop during the final copy and just after it deletes
  nothing, none after it, the refusals, a second start of either kind, the
  check at the point of no return, two admins deleting each other, the
  workspace taking no more changes while its delete runs, from requests,
  a share, the AI's tools and the jobs that change it), the database
  check and the streamed snapshot download; `test_backup_safety.py` the
  final copy itself and the delete holds.
- Frontend: `tests/tasks.test.mjs` covers the model and kinds (the share view's follow rounds too, the time left, the text recognition's rows and summaries, `ocr-tab`'s hidden time and `halt`, `ocrEnding`, `newlyFinished`'s `expected`),
  `tests/uploadBatch.test.mjs` the upload batch (its plan, question, pool, Stop and closing line),
  `tests/transferFormats.test.mjs` the export job's body and
  `tests/pageBatch.test.mjs` the library's page batches; `test_bulk_pages.py` their
  endpoint, `POST /blocks/bulk`.
- Browser: the transfers group covers a folder's annotated PDFs closed
  mid-way, reopened from the tray and downloaded, plus every export
  dialog's finished step, and an import review closed while the server
  reads it, opened from its row and imported, and one cancelled. The settings group covers a workspace export
  that downloads itself and a merged backup that reloads the library. The
  files group covers an upload stopped from the tray, a dropped folder's
  batch in its dialog, and one past a hundred files that asks first, stops
  from its row and opens its dialog again. The settings group
  also deletes a workspace through its job, turns a tab read-only on an
  edit refused for a workspace being deleted, and runs the database check
  as one; the trash group deletes twelve selected pages as a batch, in one request.
  The notes-pdf-share group keeps a folder share's view read-only from
  page to page after an edit refused for a workspace being deleted, and exports a shared page and a shared folder from
  the share view as the share's jobs, the folder's gone with its share,
  and one whose share is stopped while it runs ends as failed in the
  dialog, the tab asking no more.
