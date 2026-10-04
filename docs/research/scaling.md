# Scaling Gamma on SQLite: survey and plan

Findings from 2026-10, from one question: if the backend and frontend
could be reshaped for higher throughput and for deployments of many
users, with no need to keep existing data directories readable, what would
change? Two answers were worked out. The first replaced SQLite with one
Postgres database. The second, the one chosen, keeps SQLite and makes the
workspace the unit of placement. This note records where the design
stopped scaling, both shapes and why the second won, and the work list
with its status. The current mechanics live in the dev docs. The earlier
multi-tenant assessment is [hosting.md](hosting.md), whose four couplings
are still the reasons behind most items here.

## Where the design stopped scaling

For one person on a laptop the design is close to optimal. The limits show
with many users per deployment:

- **One process serves everything.** Rooms (`collab._rooms`), the op
  replay cache (`ops._replays`), rate-limit windows, the in-flight manifest
  walks and the job threads are module state, so a second uvicorn worker
  is impossible ([debugging.md](../dev/debugging.md) "Gotchas").
- **Writes serialize per workspace.** Every batch takes the workspace's
  write lock (`blocks_store.write_lock`, `BEGIN IMMEDIATE`) and the
  same-block merge runs under it.
- **Native PDF work runs in the web process** behind one lock
  (`pdf_text._lock`), under the same GIL as every request.
- **Every request opens a connection**, reads the journal mode, sets
  pragmas, runs the schema's create-if-not-exists statements and registers
  the SQL functions (`db._open_ws_db`).
- **Uploads are a local directory** and migrations walk every workspace at
  startup, so a deployment is one volume, one node and a non-rolling
  upgrade.
- **The HTTP stack is the slow one**: plain uvicorn (h11, the default
  loop), standard-library JSON for block trees, static files served by
  FastAPI with a hand-rolled ETag.
- **Frontend:** one 2.7 MB main chunk behind a single lazy boundary, and
  `App.jsx` at 10,872 lines holding most state.

## The two shapes

**Postgres.** One database with a workspace column on every table, row
locks per page instead of the workspace write lock, tsvector search,
object storage for PDFs, and three process roles (web, worker, realtime)
with Postgres as queue and notify bus. It makes app nodes stateless and
lets any node serve any workspace. Its costs: a database service per
deployment, a second storage backend or an embedded Postgres for the
desktop sidecar, and a port of search, migrations, backups and the mirror
engine. It was the first answer and was declined in favour of keeping
SQLite.

**SQLite with workspace affinity (chosen).** One process owns a
workspace's files, its write lock, its rooms and its caches. A process
owns many workspaces, a node many processes, and a router hashes the
workspace named by the request (`X-Gamma-Workspace`, `?ws=`, the share
token's prefix) to pick both. Requests with no workspace touch only
`users.db`. The collaboration code does not change. Idle workspaces cost
nothing, isolation is by file, and moving a workspace is a copy or a
mirror round. The accepted limits: one workspace cannot exceed one
process's write throughput, cross-workspace reports go through `users.db`,
and a cluster needs a router that understands the workspace header.

| Component | Before | Chosen |
|---|---|---|
| Placement | One process, one node | Router hashes the workspace to a process, and to a node in a cluster |
| Process roles | One uvicorn does everything | Same image, three roles: web workers, job workers, one scheduler per node |
| Rooms, replay cache | Module dicts | Rooms unchanged under affinity; the replay answer moves into the op log |
| Job queue | Threads in the web process | The `jobs` table claimed by worker processes |
| Blobs | Local uploads directory | A storage seam with an S3-compatible driver, built and then dropped: the files stay local, a bucket holds their off-site copies (item 8) |
| `users.db` | One file | Stays SQLite; replicated with LiteFS only when a second node exists |
| Migrations | Startup pass over every workspace | Per workspace on first open after an upgrade; `users.db` steps readable one release back |
| Backups | Zip snapshots on local disk | Litestream or scheduled backup-API copies to the bucket; the zip stays the export format |
| Static files | FastAPI | Precompressed assets, served by the app or a reverse proxy |

## Work list

Items 1 to 12 landed on 2026-10-03 (schema versions 32 to 34). Items 13
and 14 wait for a second node. Each item names the code it touches, and
the dev docs describe the result. Where the result differs from what was
planned, the item says so under *Built*.

1. **Cache connections per thread and file** (`db.connect_*`,
   `_open_ws_db`). The `with` contract stays (commit or roll back at the
   end) but the connection returns to a per-thread cache instead of
   closing; the WAL check, schema statements and function registration run
   once per connection. Idle eviction, a size cap, and a close-all for a
   workspace about to be deleted (open handles lock its directory on
   Windows).
   *Built:* per-thread cache capped at 8 files and 128 process-wide, idle
   120 s, closed across threads for a workspace about to be removed. A
   workspace connection went from about 2 ms to 17 µs, a session read over
   HTTP from 3.5 ms to 0.9 ms ([user_db.md](../dev/user_db.md) "Connections").
2. **Pragmas and maintenance.** `cache_size`, `mmap_size` on `pages.db`,
   `temp_store=MEMORY`, `journal_size_limit` at open; a periodic
   truncating `wal_checkpoint` and `PRAGMA optimize` per workspace from
   the app's `every()` loop, so a long reader never leaves a growing WAL.
   *Built:* `gamma/db_maintenance.py`, every 5 minutes. WALs over 16 MB are
   truncated without waiting, and `optimize` runs on cached files every 4 h.
3. **orjson** for block-dict parsing and the subtree and listing responses.
   Stored text (the `properties` column, the op log) keeps the standard
   library's output so nothing written changes shape.
   *Built:* parsing a 5,000-block tree 24 → 5 ms, encoding 27 → 2.6 ms.
   The tree reads send a stored NaN as null and a stored lone surrogate as
   U+FFFD instead of a 500 ([api.md](../dev/api.md)).
4. **The fast HTTP stack**: uvicorn's standard extras, so Linux images run
   uvloop and httptools; Windows keeps the default loop by pip's platform
   markers.
   *Built* as planned. The desktop bundle gets httptools everywhere and
   uvloop on macOS through PyInstaller's uvicorn collection.
5. **Precompressed static assets.** The build emits Brotli and gzip files
   beside the hashed assets; the static route serves the precompressed file
   when the client accepts it, and the deploy docs say how a reverse proxy
   serves them itself.
   *Built:* an inline Vite plugin (node zlib, about 5 s per build). The main
   chunk goes out as 690 KB Brotli instead of 2.7 MB, and the static route
   answers 304 for hashed assets too ([debugging.md](../dev/debugging.md)
   "Serving the build").
6. **Durable batch ids.** `page_ops` gains `batch_id` and the writer's
   remapped `cursor`, with a unique index on page, client and batch id; a
   retry finds the row under the write lock and gets the stored answer,
   across restarts. The memory replay cache goes.
   *Built* as planned (step 33). A retry whose row was pruned is applied
   again, in place of the memory cache's 10-minute TTL.
7. **Merge outside the lock, if measured.** Instrument lock hold time
   first; only if the merge path shows, read the seq and rows before the
   lock, compute, then take the lock and compare the seq.
   *Built* for text merges. Scattered edits and mirror pushes held the lock
   for 35 to 42 ms, over 90 percent of it merge compute. The merge runs
   before the lock and is reused under it when the block's text is
   unchanged (memoised by its inputs rather than a seq compare), leaving
   1.5 to 8 ms. Typing merges were never the problem. The ink merge stays
   under the lock: it writes the merged file there, which the purge
   guarantee depends on ([collab.md](../dev/collab.md) "same-block merge").
8. **The blob seam**: a local driver and an S3 driver under
   `storage.store_pdf` / `store_file` / `find_upload_file`; a lookup
   returns a cached local path; the uploads route sends a browser to the
   bucket under S3; upload GC deletes objects; backups and job artifacts go
   through the seam. Local is the default and needs no configuration.
   *Built* as a primary-store seam, a local and an S3 driver, then removed
   the same day by product decision: the app always runs on its local
   files, and a bucket is a backup target only. What stayed: the S3 client
   in `gamma/s3.py` for the off-site copies, with boto3 an optional
   dependency (`requirements-s3.txt`, about 23 MB in the image); the store
   as plain functions in `gamma/storage.py`; and a cached total in place of
   the quota walk ([user_db.md](../dev/user_db.md) "Storage limits").
9. **Share tokens carry the workspace**: a minted token is
   `<workspace id>.<secret>`, so a router can place share traffic without
   a lookup; existing tokens are rewritten by a migration step and links
   minted before it stop working.
   *Built* as planned (step 32), with one parser, `db.share_token_workspace`.
10. **Lazy per-workspace migration.** The runner splits into global steps
    (`users.db`, at startup) and per-workspace steps keyed by a
    `user_version` in each `pages.db`, run on first open after an upgrade
    under the write lock after a snapshot of that one file; a background
    warm migrates the rest. `users.db` changes stay readable by the
    previous release so two versions can serve at once.
    *Built:* `WS_VERSION_BASE = 33`, so no walk initialises the stamps and
    steps up to 33 stay eager. A workspace step is a normalizer the restore
    also runs. The container entrypoint runs `migrate --global-only`
    ([migrations.md](../dev/migrations.md)).
11. **Workspace-scoped preferences move into the workspace.** Open tabs,
    recents and reading positions become a per-user table in `pages.db`;
    they are the most frequent small writes and would otherwise forward to
    a `users.db` primary in a cluster.
    *Built* (step 34) as `workspace_prefs` in `pages.db`. The users.db rows
    stay one release, for the compatibility rule. Workspace zips empty the
    table and overwrite the freed pages, so no member's reading state
    travels.
12. **Database backups to the bucket**: a scheduled backup-API copy of
    each database through the blob seam, and a generated Litestream
    configuration for deployments that run it. The zip snapshot stays the
    user-facing export.
    *Built* as the off-site copies (below): scheduled copies with a change
    signal from file stats, 7 kept per database, `manage.py offsite` to
    list and restore them and `manage.py litestream-config`. A round over
    1,000 unchanged workspaces takes about 120 ms. Litestream itself was
    not run.
13. *Cluster only:* `users.db` through LiteFS with write forwarding.
14. *Cluster only:* a placement table overriding the hash, and a relocate
    job.

The same survey found three frontend items, not part of this list. Splitting
the main chunk by surface is built (October 2026): 2.7 MB became 1.9 MB,
with Settings, the chat, the guide, the dialogs and pdf.js fetched on first
use ([bundle.md](bundle.md) has the measurements and what stayed in, and
[frontend-refactor.md](../dev/frontend-refactor.md#lazy-boundaries) the
rule). Not built: state owners with a normalized block map (the owners are
planned in [frontend-refactor.md](../dev/frontend-refactor.md)), and one
multiplexed socket per tab.

Follow-ups built the same day, after the cleanup pass: the API refuses
NaN and Infinity with a 400 before anything is written (`ops.storable`),
and the upload purge checks only the due names under the write lock
(`storage.stat`) instead of listing the workspace.

Then the bucket became a backup target only. The app always runs on its
local files, and the bucket store of item 8 was removed with everything
that served it. Item 12's copies became the off-site copies
(`gamma/offsite.py` over `gamma/s3.py`): they send the uploaded files
too, an admin sets them in Settings → Backups or the environment does,
and `manage.py offsite` restores them
([debugging.md](../dev/debugging.md#off-site-copies-in-a-bucket)).

What stays as it is: the block table and its hot columns, the per-page op
log and `seq`, the external-content FTS5 index maintained by triggers, the
split between `pages.db` and `data.db`, and content-hashed upload names.
