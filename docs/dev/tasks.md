# Background tasks

Long work runs in the background and shows in one place: the Background
tasks button in the topbar (on a phone, a row of the More sheet). Two kinds
of work meet there:

- **Server jobs**: exports, workspace exports, snapshots, restores, library
  imports, the admin's server snapshots and the search indexer. A job lives
  on the server (`gamma/jobs.py`), so it goes on when the tab or the dialog
  that started it closes, and every tab and device of the account sees it.
- **This tab's own work**: a PDF loading in the viewer, a file uploading,
  an AI lookup (metadata, a title, a slide citation), a translation. These
  rows exist in this tab only; they stop with the tab.

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
process. The row holds the kind, the owner (the account that started it, or
`""` for a workspace's own work), the workspace, an English title, the
parameters (JSON, what a window needs to show the job again), the state
(`queued`, `running`, `done`, `failed`, `cancelled`), the last progress, the
result (JSON), the error, and the file the job produced. A running job's
progress lives in memory and is laid over the row on every read, so a
progress report writes nothing. The final row is written before the job
leaves the live set, so a read never sees a job ended without its result.

The endpoint of the work authorizes the request, then calls
`jobs.start(kind, owner=…, ws=…, title=…, params=…, run=fn)`. `fn(job)` runs
in the worker thread and returns the result. It reports through
`job.progress(done=, total=, unit=, phase=, item=)`:

- `unit` names what `done` and `total` count: `pages`, `bytes`, `files`,
  `items`, `papers`, `workspaces`.
- `phase` is a short key the interface words (`packing`, `typesetting`,
  `unpacking`, `checking`, `saving`, `copying`, `restoring`, `annotations`,
  `databases`, `files`; `PHASES` in `tasks/taskKinds.js`).
- `item` is the thing at hand, a page title or a workspace name.
- A new phase or unit starts its own counts; a field left out is kept.

Every report is also where a job stops: once the job was asked to stop,
`progress` raises `jobs.Cancelled`. It is a `BaseException`, so an import's
`except Exception` around one item never swallows it. `stoppable=False`
marks the point after which the job can no longer stop; a restore reports
it just before it swaps or merges the databases. A job that writes a file
writes it to `job.artifact_path` and names the download with
`job.set_artifact(name, media_type)`. An exception fails the job: an
`HTTPException`'s detail, a `ValueError` or an `OSError` is the message as
it stands; anything else is logged with its traceback.

The work itself takes the report as a plain `progress` callable, never
the job: `ws_backup.write_zip`, `create` and `restore_zip`,
`backups.create`, the export driver `_run_export`, the Zotero and Markdown
imports. A caller that does not watch passes nothing (the routers default
to `jobs.no_progress`).

**Limits.** An account runs two jobs at once and the server four; the rest
wait queued, oldest first. A workspace's own jobs (the indexer) run outside
those limits. An account may have 20 queued or running jobs (429 past
that). A `key` makes two jobs of one owner and kind the same work: while
one is queued or running, starting another raises `jobs.Busy` with it (the
indexer, one per workspace; an import, one per review).

**Files.** A job's file is `<data dir>/jobs/<id>/artifact`; an upload a job
reads (a restore's zip) waits in `jobs/incoming/` and the job removes it
once read. A job that writes a file does not start while the disk has less
than 1 GB free or the account's finished files take 10 GB (507, "download
them and remove them in Background tasks first"). Files are not metered
against the account's storage quota.

**Ending.** A finished job is kept 24 hours (`RETENTION_S`). The sweep
(`jobs.sweep`, every 10 minutes from the app lifespan) removes it with its
file, job directories no row names and uploads no job took. At startup
`jobs.recover` fails the jobs a stopped process left queued or running
("interrupted: the server restarted while this ran"). A process only
recovers rows another process stamped (`instance`), so a second app
lifespan in the same process never fails its own jobs. The jobs run in the
one server process, like the page sockets. A deleted account's jobs go with
it (`workspaces.delete_account`); a renamed account's follow it
(`admin.rename_account_rows`, `jobs.renamed`).

## The API (`routers/jobs.py`)

Only a session account uses jobs (integration tokens get 403, like every
account-level endpoint). A job is its starter's business: another account
gets 404 for it. A workspace's own jobs are seen by the workspace's members
while they work in it (the request's workspace), and stopped or removed by
its editors and owners. Endpoints and shapes: [api.md](api.md) "Background
jobs".

- `GET /api/jobs` lists the account's newest 100 jobs without their
  results, plus the request workspace's own. Scheduled backups keep their
  own state (`gamma/backup_schedule.py`), so the listing adds a read-only
  row (`scheduled-backup`) for each of the account's tasks while it is
  queued or running. A finished or failed run stays in Settings → Backups;
  a failure raises the `backup-failed` notice.
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
| `export` | `export` (`routers/export.py`) | the Export dialog, for a page or a folder | the file; `{pages, skipped}` |
| `import` | `import` (`routers/imports.py`) | the import review's Import to library | the import report |
| `workspace-export` | `workspace-export` (`routers/ws_backups.py`) | Settings → Workspaces → Data → Export…, Export all | the zip |
| `snapshot` | `snapshot` | Settings → Backups → Back up now, Back up all | `{snapshots, failed}` |
| `restore` | `restore` (an uploaded zip), `restore-snapshot` | Settings → Workspaces → Data → Merge / Restore, Settings → Backups → Restore, a shared page imported by link | the restore report |
| `server-backup` | `server-backup` (`routers/admin.py`) | Settings → Server → Server backups | the snapshot's info |
| `indexing` | — (`routers/search.py`, owner `""`) | a search, a page chat, Settings → Maintenance → Rebuild | `{papers}` |

The export driver reads a folder's pages again when the job runs, so a
queued export holds what the folder holds then. An import job holds the
staged review's claim while it reads the upload and removes the upload when
it ends, however it ends. Asking again for the same review answers the job
already started (409 with another selection). The indexer keeps one row per
workspace (`jobs.prune`). Synchronous endpoints serve scripts, the share
view and the MCP export: `/api/export`, `/export-all`, `/import-data`,
`/pages/{id}/export`, `/folders/export` and the snapshot endpoints. They
run the same code as the jobs.

## The web app (`frontend/src/tasks/`)

`useTasks.js` is the store, one instance in App.jsx:

- It polls `GET /api/jobs` every 1.5 s while a job runs or waits, or while
  something watches (the tray open, a dialog on a job: `watch()`). Otherwise
  it polls every 30 s, and never while the tab is hidden. An older answer
  never replaces a newer one.
- `start(route, body, meta)` and `upload(route, form, {name, meta})` start
  a job, known at once, before the next listing. An upload is a row of its
  own until the server answers with the job. `meta.download` is `auto`
  (fetch the file once it is ready), `offer` or `none`.
- When a job ends, the store fetches its file if asked, then calls
  `onFinished(job, started)` once per job. That includes a job that ended
  before any listing saw it running.
- This tab's rows: `addLocal`, `updateLocal`, `reviveLocal`, `removeLocal`,
  and `cancel` to stop one. A late report of work the person stopped never
  overwrites "cancelled".

`taskModel.js` and `taskKinds.js` are pure, tested in
`tests/tasks.test.mjs`. `taskModel.js` holds the states, the order (what
runs first, oldest on top; then what ended, newest on top) and the tray
badge. `taskKinds.js` gives each kind its icon, title, detail line and row
actions, where a click opens it (`open`) and how to start it again
(`retry`: the same route and body).

`TasksTray.jsx` holds the button, the panel and its rows:

- The button shows a spinner while anything runs, a red dot for a failure
  worth a look, and an accent dot for a file waiting to be downloaded.
- The panel lists In progress, then Finished, with Clear finished.
- A row has its title, elapsed or finish time, bar and detail, plus Stop,
  Download, Start again and Remove. A click on an export or import row
  opens its dialog on that job again; a backup row opens Settings → Backups.
- `TaskProgress` is the progress block the dialogs and the Backups pane
  show.

`App.jsx` `onJobFinished` says what a window does not show:

- A file a closed dialog left behind is offered in the pill with a
  Download button.
- A restore into the open workspace reloads the tab that started it, and
  offers a Reload to any other tab.
- An import into the open workspace refreshes the library.
- A job started from a menu shows its progress in the pill while it runs
  (`meta.pill`).

The Export dialog's last step follows the export job
([import_export.md](import_export.md) "The Export dialog"). It shows
progress and Stop while the job runs. Once the file is ready, the dialog
downloads it, lists the pages left out and says what to do next. It may
close any time, and the job's row opens it again. The import review
follows its import job the same way.

A share view has no background tasks. Its exports download directly
through the synchronous endpoints.

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
  as a job and the annotated-PDF folder zip; `test_backup_jobs.py` the
  snapshots, restores and server backups. The import job is tested in
  `test_import_review.py` and the indexer in `test_search.py`.
- Frontend: `tests/tasks.test.mjs` covers the model and kinds, and
  `tests/transferFormats.test.mjs` the export job's body.
- Browser: the transfers group covers a folder's annotated PDFs closed
  mid-way, reopened from the tray and downloaded, plus every export
  dialog's finished step. The settings group covers a workspace export
  that downloads itself and a merged backup that reloads the library. The
  files group covers an upload stopped from the tray.
