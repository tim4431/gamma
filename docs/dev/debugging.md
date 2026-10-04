# Running, testing & debugging

## Run it

Backend (FastAPI, Python 3.11+):

```bash
cd backend
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python manage.py setup          # idempotent: missing personal workspaces + workspace files
uvicorn app:app --host 127.0.0.1 --port 9001 --reload
```

The requirements install uvicorn with its standard extras, so its automatic
choices take httptools for HTTP and, on Linux and macOS, uvloop for the event
loop (pip skips uvloop on Windows by its platform marker); `--reload` watches
with watchfiles. No command names them.

Frontend (React + Vite):

```bash
cd frontend
npm install
npm run dev     # :5173, proxies /api → 127.0.0.1:9001
npm run build   # outputs dist/ (FastAPI serves it in the Docker image)
```

First run: the app seeds an `admin` account with a random password printed
once to the console (only while zero non-guest accounts exist). User CRUD
also via `python manage.py` (create-user, set-password, set-admin,
rename-user, delete-user, list-users, list-workspaces, set-member,
sweep-guests, migrate, backups, offsite, litestream-config).

Docker:

```bash
docker build -t gamma .
docker run -p 9001:9001 -v gamma-data:/data ghcr.io/tim4431/gamma
```

### Serving the build

`npm run build` writes a Brotli and a gzip copy beside each compressible file
in `dist/` ([pdf_loading.md](pdf_loading.md), "The worker"). Wherever the
backend serves the build (`GAMMA_STATIC_DIR`: the Docker image, the desktop
app, a manual install) it sends the copy a request accepts, so a reverse
proxy in front of it needs no compression of its own for those files. A
proxy that serves `dist` itself, passing only `/api` (with its WebSockets) to
the backend, sends the copies with Caddy's `file_server { precompressed br
gzip }` or nginx's `brotli_static on; gzip_static on;` (`brotli_static` comes
with the ngx_brotli module). It then also owns the backend's other static
rules: `index.html` for any path that is no file, `/assets/*` cached
`public, max-age=31536000, immutable`, everything else `no-cache`.

### Off-site copies in a bucket

Gamma always works on the files in its data directory. An S3-compatible
bucket (AWS S3, Cloudflare R2, MinIO, Backblaze B2) can hold copies of the
databases and the uploaded files, so a lost disk costs at most one interval
of work (`gamma/offsite.py`, `gamma/s3.py`). The bucket is a backup target
only: nothing reads from it except a restore. It needs boto3: the Docker
image installs `requirements-s3.txt` beside `requirements.txt` (about
23 MB, most of it botocore's service models), and a checkout runs `pip
install -r requirements-s3.txt`. The desktop app does not include it.

**What is copied.** A round runs at startup and then once per interval:

- users.db and each workspace's pages.db and data.db that changed since its
  last copy, to `<prefix>offsite/users/<stamp>.db` and
  `<prefix>offsite/<workspace>/<stamp>-pages.db` / `-data.db`. `<stamp>`
  is the round's UTC time, such as `20261003T140000Z`, and is the same for
  every copy the round takes. The newest copies of each database are kept
  (7 by default).
- Each uploaded file the bucket does not hold yet, to
  `<prefix>uploads/<workspace>/<name>`. A name is the file's content hash,
  so a file is sent once and has one copy.

**Settings.** Admins set the copies in Settings → Backups → Off-site copies
([settings.md](settings.md)): on or off, the bucket, the endpoint (empty
for AWS; R2's `https://<account>.r2.cloudflarestorage.com`, MinIO's
address; a bucket behind its own endpoint is addressed by path), the region
(R2: `auto`), the access key and secret key (both empty: boto3's own chain,
the `AWS_*` variables or an instance role), the prefix (put before every
key, for a bucket several servers share), the interval (default 3600 s, at
least 60) and the copies kept per database. Test lists the bucket once with
the form's values, saved or not. Copy now starts a round at once. It is the
server's own round, not a background job ([tasks.md](tasks.md)), and the
section's status line follows it: the last round, what it copied, what
failed and why, and the next round. The settings are stored in users.db
under the `settings` key `offsite`, the secret key encrypted with the data
directory's key like the shared AI provider keys. A round reads them as it
starts, and the pause after a round is read as the round ends. So a change
applies from the next round, and a new interval from the pause after it.
On a hosted container the plan's `offsite` answer holds the interval and
the copies kept to its own, from either source. Its values fill what the
environment leaves unset, and otherwise win only where they are stricter:
a shorter interval, more copies ([cloud_accounts.md](cloud_accounts.md)
"Hosted containers").

**From the environment.** When `GAMMA_S3_BUCKET` is set, every setting
comes from the environment and the pane shows them read-only:
`GAMMA_S3_BUCKET`, `GAMMA_S3_ENDPOINT`, `GAMMA_S3_REGION`,
`GAMMA_S3_ACCESS_KEY`, `GAMMA_S3_SECRET_KEY`, `GAMMA_S3_PREFIX`,
`GAMMA_OFFSITE` (on unless `0`, `false`, `no` or `off`),
`GAMMA_OFFSITE_INTERVAL` (seconds, default 3600, at least 60) and
`GAMMA_OFFSITE_KEEP` (default 7).

**The bucket.** Only the server talks to it, so it needs no CORS rule and
no public access. The key must be allowed to list, read, write and delete
under the prefix (pruning deletes). Two servers that share a bucket need a
prefix each.

**A round in detail.**

- **A database copy.** Each database is copied with the SQLite backup API
  into `backups/.offsite/`, which is consistent while the server writes.
  The copy is quick-checked, uploaded (boto3 sends it in parts past 8 MB)
  and then deleted. A copy that fails its check is not uploaded: the log
  gets a warning, the admins get the `db-damage` notice, and the next round
  tries again.
- **Only what changed.** The round compares the mtime and size of each
  database file and of its WAL with what they were at its last copy, which
  `backups/offsite.json` records. That is two stats per database, without
  opening it. Before a copy, a WAL with frames in it is checkpointed and
  truncated if nothing holds it (the maintenance tick's checkpoint, which
  never waits), so the server folding the WAL in later does not count as a
  change. users.db changes with every sign-in, so it is copied in most
  rounds. Measured 2026-10 on Windows: with 1,000 empty workspaces (2,001
  databases), a round that finds nothing changed takes about 120 ms.
- **Uploads.** The state file also records the mtime of each workspace's
  `uploads/` directory, which changes when a file is added or removed.
  Only a workspace whose directory changed, and every workspace in the
  first round, has its objects listed (one listing, not a request per
  file). Each local file the bucket lacks, or holds at another size, is
  then streamed up. The directory's mtime is recorded only when all of
  them went up, so a file that failed is tried again next round. The
  databases go first, so every file a database copy names is in the
  bucket once the same round's uploads are done.
- **Failures and the log.** A copy or an upload that fails is logged and
  the round goes on with the others. A round writes one `[offsite]` line
  when it copied or failed something, as a warning when something failed.
  Settings that name no bucket that can work (boto3 missing, half a key
  pair) stop the round before it starts, with the reason in the status.
- **Pruning.** After a round copies a database, its copies past the number
  kept are removed. A data directory never removes copies older than its
  own first round. So a server started on an empty volume (a lost disk,
  restarted before anyone restored it) copies its fresh users.db without
  pushing out the copies of the lost one. A restore makes the older copies
  the directory's own, and pruning goes on as usual.
- **What stays in the bucket.** A file deleted from a workspace, and the
  copies and files of a deleted workspace, stay in the bucket. Remove
  `offsite/<id>/` and `uploads/<id>/` by hand or with a lifecycle rule.
- **What is not copied.** The job artifacts, the server snapshots and the
  workspace snapshot zips stay on the node's disk. `publisher-sessions.key`
  in the data directory (or `GAMMA_PUBLISHER_SESSION_KEY`) is not copied.
  It encrypts the saved AI keys, the Gamma Cloud grants, the mirrors'
  tokens, the publisher sessions and the off-site secret key in users.db,
  so keep it somewhere else, or a restored server cannot read those (the
  secret key can be typed in again).

**Restore**, with the server stopped. The command uses the bucket the
settings name. After a lost disk the saved settings went with users.db, so
give the bucket in the `GAMMA_S3_*` variables for the command; the restored
users.db brings the saved settings back for the server. `manage.py offsite
--list` shows each database's copies (how many, the newest), and `--list
<workspace>` or `--list users` lists every copy of one, with how many of a
workspace's files the bucket holds. Then run `manage.py offsite --restore
<workspace|users|all> [--at <stamp>] [--uploads]`. `all` restores users.db
and every workspace the bucket holds copies of, which is the way back
after a lost disk. Each database gets its newest copy, or with `--at` its
newest copy from that round or earlier. Because a database is copied
whenever it changes, that is what it held at that round. A workspace's
data.db without such a copy is left as it is, since everything in it is
rebuilt. `--uploads` also downloads each restored workspace's files that
its `uploads/` lacks, before any database is replaced; the files already
there stay as they are. The command refuses, changing nothing, in these
cases:

- users.db or a database it would replace is open, which an exclusive open
  detects: a running server holds the files it used lately.
- A download fails its quick check. Every copy is downloaded beside its
  database first.
- Without `--at`, a database has copies from both before and after this
  data directory's first round. Then a server ran on an empty disk, and its
  own copies are the newest. The message names the `--at` that takes the
  newest copy from before.

Then each database in place is moved aside with its WAL, as
`<name>.pre-restore-<time>`, and the copy takes its name. A restored
pages.db's unreferenced-file clocks start over, as with a server backup.
Start the server; it migrates a copy older than this Gamma. Delete the
`.pre-restore-` files once the restored server works. `offsite` and
`litestream-config` run before the schema check, on a data directory of
any version or none. In Docker (leave `$S3` out when the compose file
sets the `GAMMA_S3_*` variables):

```bash
docker compose stop gamma
S3="-e GAMMA_S3_BUCKET=gamma-backups -e GAMMA_S3_ENDPOINT=https://<account>.r2.cloudflarestorage.com
    -e GAMMA_S3_REGION=auto -e GAMMA_S3_ACCESS_KEY=... -e GAMMA_S3_SECRET_KEY=..."
docker compose run --rm --no-deps --entrypoint python $S3 gamma manage.py offsite --list users
docker compose run --rm --no-deps --entrypoint python $S3 gamma manage.py offsite --restore all --uploads
docker compose start gamma
```

The server backups (`manage.py backups --restore`,
[migrations.md](migrations.md#backups-gammabackupspy)) are a different
thing: whole-directory snapshots on the node's own disk.

#### Litestream

Litestream streams each database's WAL to a bucket within seconds of a
write. The off-site rounds above are the simpler default; choose
Litestream when seconds of loss matter. It copies no uploaded files, so
keep the rounds on for those. It runs beside Gamma, not in the image.
`manage.py litestream-config [--out <path>]` writes a `litestream.yml`
(Litestream 0.5 or later, one `replica` per database) for the off-site
bucket, whether it was saved in the pane or set by the environment. It has
an entry for users.db and for each workspace's pages.db and data.db that
exist, replicated to `<prefix>litestream/users.db` and
`<prefix>litestream/<workspace>/<name>`. The keys are never written into
the file: with an access key it names `${GAMMA_S3_ACCESS_KEY}` and
`${GAMMA_S3_SECRET_KEY}`, which Litestream expands from its own
environment. Give the Litestream container those two variables even when
the bucket was set in the pane. Write the file inside the container, so
its paths are the container's:

```bash
docker exec gamma python manage.py litestream-config --out /data/litestream.yml
```

Run Litestream as a sidecar on the same volume, at the same path, with
the same keys:

```yaml
  litestream:
    image: litestream/litestream
    command: replicate -config /data/litestream.yml
    volumes:
      - gamma-data:/data
    environment:
      GAMMA_S3_ACCESS_KEY: ...
      GAMMA_S3_SECRET_KEY: ...
    restart: unless-stopped
```

Litestream does not watch its configuration. Run the command again and
restart the sidecar (`docker compose restart litestream`) whenever a
workspace is added or deleted; a workspace created since is not
replicated until then. Litestream 0.5 can also follow a directory itself
(`dir:` with `watch: true`), which this command does not write. To
restore, stop Gamma and Litestream, move the database and its `-wal` and
`-shm` aside (Litestream does not write over an existing file), and run
`docker compose run --rm litestream restore -config /data/litestream.yml
/data/workspaces/<id>/pages.db` for each file. The rounds and Litestream
can run together, under `offsite/` and `litestream/`.

## Tests

### Local changes: test the affected modules

Default to the smallest set of tests that covers the changed behavior and its
direct consumers. Do not run the full backend suite, all frontend tests, or
the full browser suite after every edit. Once relevant checks pass, repeat
them only after further relevant changes or when a failure needs investigation.

- Backend changes: select the relevant `tests/test_*.py` files. Prefer whole
  files because tests within a file can depend on earlier tests. For a few
  files, omit `-n auto` to avoid starting a worker per CPU.
- Frontend pure-module changes: invoke `node --test` with the relevant test
  files directly. `npm test` always includes the full module suite.
- UI behavior changes: build once after the final frontend edit so the suite
  sees current code, then `npm run e2e -- --changed`, which runs the groups
  the working tree's changes select (see [the browser suite](#browser-end-to-end-suite);
  `--changed main --list` shows a branch's selection without running it).
  Narrow further with `--group` or `--only` when the selection is broader
  than the change, e.g. a one-line edit in a shared module selects every
  group. Check that the intended steps actually ran. A pure-module change
  does not automatically require a build or browser run.
- The iPad app: a change to the sync protocol, the uploads routes, the
  integration tokens or the session's answer also runs the iPad checks
  ([ipad.md](ipad.md) "Keeping the host in step": the contract test,
  the core bundle's test and the `replica` browser group).
- Shared contracts and helpers: include tests for affected consumers. For
  example, changes to shared normalization cases need both Python and Node
  coverage; auth, workspace access, migrations, and block storage may require
  several related suites. Broaden further when the impact cannot be bounded.
- Documentation-only changes: check the diff and referenced commands/paths;
  application tests and builds are unnecessary.

Examples (paths are relative to the indicated directory):

```bash
# From backend/: OAuth behavior and its MCP integration
python -m pytest tests/test_mcp_oauth.py tests/test_mcp.py -q

# From frontend/: settings module behavior
node --test tests/settings.test.mjs

# From frontend/: UI behavior (build once before the browser run)
npm run build
npm run e2e -- --changed          # the groups these changes select
npm run e2e -- --group settings   # or name them
```

Report which checks ran and any relevant coverage gaps. Full suites remain
the PR CI safety net in `.github/workflows/check.yml`; run them locally for
broad changes, an explicit request, or unresolved regression concerns.
Only the browser suite selects from the changed files (`--changed`); backend
and pure-module tests are still chosen by hand.

### Full backend suite

```bash
cd backend
pip install -r requirements-dev.txt   # pytest, pytest-xdist, httpx
python -m pytest tests -q -n auto --dist loadfile   # parallel, ~15 s
python -m pytest tests -q                           # serial, ~50 s (simpler tracebacks)
```

In-process API tests (FastAPI TestClient) against a throwaway data
directory — no server, no network: an autouse fixture in `conftest.py`
refuses every non-loopback socket connect and DNS lookup, so a test that
forgets to stub a metadata / PDF / AI fetch fails at once instead of
passing slowly on the network. `pytest.ini` names `tests/`, so a bare
`pytest` from `backend/` works too. The suite runs in parallel with
pytest-xdist: every worker process imports `conftest.py` and so gets its own
throwaway data directory, and `--dist loadfile` keeps each file's tests on
one worker in file order (tests inside a file may build on each other;
files never may). The shared `client` fixture carries the cookie of the last
login on that worker, so a "not signed in" check uses the `anon` fixture (a
fresh client), never `client`. One data directory serves every file on a
worker, so an account name belongs to the module that creates it: prefix
names with the module's area (`bk_admin`, `ca_alice`), create them through
`conftest.make_user`, and pick folder names no other module uses in the
`guest` fixture's workspace. That fixture's account is
`conftest.guest_name()`, a fresh `guest-…` name per run; never write
`"guest"`. `make_user` fails the run when a second module asks for a name
another module already created. Run them with the project venv's
interpreter (`venv/Scripts/python.exe` on Windows): the two vector-math
tests need `ziamath` from `requirements.txt`, and a system/conda `python`
without it fails them with "ziamath is not importable" rather than a
puzzling path count.

`conftest.py` also holds what several files share: the `data_dir` fixture
(a data directory of the test's own, for the migration, backup and startup
tests that must not touch the worker's shared one), `at_once` / `together`
(callables in threads started on one barrier), `slowed` (widens a writer's
window between its check and its write, so a race test fails without the
lock) and `recv` (the next message of a kind on a page socket).

The AI agent's tests are split by area — `test_ai_tools_registry.py`
(scopes, permissions, the system prompt), `test_ai_tools_pages.py`,
`test_ai_tools_blocks.py`, `test_ai_tools_search.py` (the executors),
`test_ai_wire.py` (provider wire formats, SSE parsing, history replay; pure)
and `test_ai_agent_loop.py` (`/api/ai/chat` with a faked provider) — over the
fixtures in `tests/ai_fixtures.py`. Its `org` fixture creates one account
per test module (the module's name is in the username), so the files never
see each other's pages or provider entries.

Rules the frontend mirrors — search normalization (`gamma/textnorm.py` ↔
`frontend/src/shared/lib/textnorm.js`), published pages' slugs
(`gamma/publish.py` ↔ `frontend/src/shared/lib/slug.js`) and text boxes
(`gamma/text_box.py` ↔ `frontend/src/markup/textBox.js`,
`tests/shared/textbox.json`, [text_boxes.md](text_boxes.md)) — are pinned by ONE
set of cases both sides read: `tests/shared/*.json` at the repository root,
run by `backend/tests/test_shared_fixtures.py` (the slugs by
`test_publish.py`) and the matching node tests. Add a case there when a rule
changes; whichever side drifts fails.

The frontend has **no linter** and no component tests. Its pure modules have
`node --test` tests (`npm test` from `frontend/`, the files in
`frontend/tests/*.test.mjs`): `blockOps` (diff/apply), `collabSession` (the
page session's transport logic over fakes — ordering, reconciliation,
retries, presence, the caret throttle), `sessionState`, `settings`
(navigation, presets), `textnorm` and `libraryUtils` (the shared cases
above), `mdMarks` (the formatting hotkeys' toggle), `logseqPdfModel` (tree
ops), `blockHistory` (the undo classifier), `menuAim` (the safe-triangle
geometry), `themes` (the design tokens: the desktop copy, the pre-paint's
theme lists, every theme resolving, the contrast ratchet) and
`designTokens` (the raw-value ratchet on the stylesheets). Both ratchets are
described in [ui-design.md](ui-design.md#ratchets). A module is testable
there when its relative imports carry the `.js` extension (node resolves
nothing else). Modules that import React can still be imported for their
pure exports. Actual React rendering and
interactions are exercised by the browser suite below, plus one standalone
browser regression: `npm run e2e:latex` bundles the block
editor with esbuild over an in-memory fixture ([latex_editing.md](latex_editing.md)).

### Browser end-to-end suite

```bash
cd frontend
npm run build                   # the suite drives frontend/dist
npm run e2e                     # parallel workers, ~2 min on 6; exit 1 on any failure
npm run e2e -- --jobs 1         # one worker, groups in order, live output (~8 min)
npm run e2e -- --changed        # only the groups the working tree's changes select
npm run e2e -- --changed main --list  # a branch's selection and why, without running
npm run e2e -- --group ink,collab     # these groups (ids: `--list`)
npm run e2e -- --only collab    # steps whose name contains "collab" (one worker)
npm run e2e -- --continue       # keep going after a failure
npm run e2e -- --headed         # watch the browser
npm run e2e -- --keep           # keep the temp data dir + server.log
```

High-zoom tablet regressions: `npm run e2e -- --only "pdf touch" --keep`.
`GAMMA_E2E_BROWSER=webkit` (after `npx playwright install webkit`) runs the
suite in WebKit. Native touch gestures need Chromium's CDP, so under WebKit
the touch scenario checks only the 400% PDF/ink paint and bitmap release at
tablet dimensions and DPR 2. That is browser emulation, not an iPad measurement.

`frontend/tests/e2e/run.mjs` starts an ISOLATED backend (the project venv's
python — or the interpreter `GAMMA_E2E_PYTHON` names — over a fresh
`GAMMA_DATA_DIR` under the OS temp dir, on a free port, serving a copy of
`frontend/dist` taken at start, so a build during the run cannot break its
page loads, with the update check, the models.dev catalog and the paper
registry lookups switched off so no step waits on the internet), creates
the accounts `alice` / `bob`, and drives Playwright's Chromium
(`playwright` is a devDependency; the browser is downloaded once on first
launch). A failed step saves a screenshot of every open page plus the
pages' recorded problems and the server log's tail under the temp dir's
`failures/`, and the temp dir is kept (the summary prints its path). A page
the step closed on its way out (the usual `finally { await ctx.close() }`)
is there too, as `-closed.jpg`: closing a context or a page during a step
first keeps its last look. The
`check` workflow runs the suite on every PR and uploads those folders as the
`e2e-failures` artifact. `harness.mjs` holds the server lifecycle, `Account` (session
cookie + `X-Gamma-Workspace` for API seeding, browser contexts logged in as
that account), `makePdf` (a small real PDF with a text layer), and `step()`.

The run is split into groups (`GROUPS` in `tests/e2e/select.mjs`, one per
scenario file except `notes-pdf-share`, whose scenarios hand data along;
`run.mjs` maps each id to its scenario functions). `--jobs N` (default:
half the CPUs, at most 6; one with `--only`) forks N workers, each with its
own backend, data dir and browser; a worker takes the next group off the
queue, longest first, and the group's lines print as one block when it
ends. A group must not rely on another group's data: put scenarios that do
in the same group. Workers reuse their accounts between groups: restore an
account preference changed for one scenario in `finally`, after closing its
browser context. Otherwise a later group can inherit a different Enter-key
binding or other setting. To check this isolation, run the affected groups
together with `--jobs 1` as well as on their own. Tour scenarios must also
set up prior tour progress explicitly when an earlier automatic offer would
take the one-offer-per-load slot (for example, the windows offer on a PDF
before testing citations).

Without `--continue` a failure stops handing out groups
and the ones running finish. The wall time is bounded by the longest group
(settings and the first-run guide, about a minute each), so split one of
those before adding workers.

`--changed [ref]` picks the groups from what changed: the working tree
(staged, unstaged, untracked) against `HEAD`, or everything since the branch
left `ref`. `RULES` in `select.mjs` maps each source path (first matching
glob wins) to the groups that exercise it: `frontend/src/guide/**` to the
three tour groups, `backend/gamma/ink.py` to the ink groups,
`frontend/src/markup/**` (the page layers and the tool strip, whose
anchors the ink tour uses) to the ink, ink-editing, pdf-touch, notebooks,
triggered-guide and textbox groups, docs, desktop,
extension and backend tests to none, and what every scenario goes through
(`src/app/`, `src/shared/`, the home library, the page's live session, the
backend core: auth, db, blocks, ops, uploads) to all of them. A changed
scenario file also selects every scenario that imports its helpers, and a
changed line naming a `data-guide` / `data-tour` anchor selects the tours
whatever file it is in. The run prints each selected group with the files
behind it. `tests/e2eSelect.test.mjs` fails when a file under
`frontend/src`, `frontend/public` or `backend/gamma` reaches only the
catch-all (a new folder or module needs its rule), when a group's scenario
files drift from `scenarios/`, or when a rule names an unknown group. When a
change reaches less than its rule says (a new prop threaded through
App.jsx into one pane), the selection errs wide: narrow it with `--group`.
CI runs everything.

**Flaky steps.** A step that passes here and fails on CI is almost always a
timing assumption that a slow machine breaks, and the CI runner has 4 cores.
To reproduce that, pin a run to 4 cores. On Windows, start
`node tests/e2e/run.mjs --continue --jobs 3` with `Start-Process -PassThru`
and set `.ProcessorAffinity = 0xF` at once (the workers, backends and
browsers inherit it). On Linux, use `taskset -c 0-3`. The patterns behind
past flakes, and their fixes:

- Checking once right after an action (`assert(await x.count() === 1)`):
  poll with `until()` or wait for the locator instead.
- Acting while something is still moving (a touch fling's momentum, a
  resize): wait for two equal readings first.
- Racing a background job (an indexer still running, the backup scheduler):
  wait for its state, never for a fixed time.
- A slow round the app runs by itself (a 20 s poll, a 30 s scheduler round):
  trigger it the way a user would (the tab regaining visibility), or make
  the app start it at once when that is the right behaviour anyway.

Open: `ink edit: pen resumes writing…` once missed a tap on the stroke on a
loaded 4-core run. Stale layout, a long-press cancel and the undo's
pending delete were each ruled out. When a tap opens no menu, `tapInk`
fails with what was under the tap.

The scenarios live in `tests/e2e/scenarios/`:

- `mermaid.mjs`: note/chat diagrams, streaming fences, editing, source copying,
  SVG downloads, theme changes and Markdown round trips. Run with `--only mermaid`;
  implementation details in [mermaid.md](mermaid.md).
- `mentions.mjs`: paper search, keyboard and touch selection, reference limits,
  persistence, PDF receipts and textarea shrink after clearing context. Run with `--only mentions`.
- `chatNavigation.mjs`: a library or PDF chat reply keeps streaming and is
  saved while the user navigates away and back, before or after it finishes.
  `--only "chat navigation"`.
- `publish.mjs`: publishing a page to Gamma Cloud end to end. A second
  Gamma is started as the share host (`new Server({env})` in `harness.mjs`
  passes `GAMMA_CLOUD_ISSUER`, `GAMMA_CLOUD_POLICY=provision`,
  `GAMMA_CLOUD_SHARE_HOST=1`), and `fakeCloud.mjs` stands in for the
  account server both servers trust: discovery naming the share host, an
  authorization endpoint that answers at once, PKCE code and refresh
  grants, EdDSA ID tokens with their JWKS, `/userinfo`, and the profile and
  server-list endpoints. The account links its identity through the real
  round trip from the share popover, publishes, opens the link on the share
  host anonymously, changes the cloud share's audience, syncs, checks the
  pill and Settings' Publishing row, unpublishes, and stops publishing
  from Settings. `--only publish`.
- `mirror.mjs`: Settings → Workspaces → Clones — the server clones one of
  its own workspaces through the dialog with a write token made via the
  API: Sync, the empty conflicts list, opening the clone, the sync pill's
  log and settings (cadence, detach, reattach), a same-block conflict
  resolved on its row chip, Remove origin ([mirror.md](mirror.md)).
  `--only mirror`.

- `notes.mjs`: New page → title → first block (the seed-block insert),
  Shift+Enter / Tab / Shift+Tab / Backspace, Enter as a line break vs the
  Enter-as-new-block preference, Ctrl+Z, the handle menu, todo checkboxes,
  an uploaded image (its URL must carry the workspace), the workspace
  switcher. Runs in a NON-default workspace on purpose.
- `pdf.mjs`: upload + page by attachment, the viewer's text layer, a
  highlight from a text selection (overlay, quote row, persisted position),
  the find bar hitting page 2, an AI citation link
  highlighting its quote on the cited page ([pdf_citations.md](pdf_citations.md)),
  the translate button and the selection popup's translator (against a
  mocked `/api/ai/translate`).
- `transfers.mjs`: the Import and Export dialogs — format/source cards,
  the review step and its switches, direct export for fixed formats, the
  export job's finished step, and a folder's annotated PDFs exported in the
  background: the dialog closed mid-way, reopened from Background tasks and
  its zip downloaded ([tasks.md](tasks.md)). The job's listing is held at
  "running" with `page.route`, since the server's job is quick.
- `ink.mjs`: handwriting. The tool strip and its presets, mouse strokes
  becoming an ink block with an `.ink` upload, persistence across a reload,
  the eraser, stroke undo/redo, the partial eraser, a lasso move + delete,
  the notes card's jump + outline (`/Ink` in the exported PDF is `test_ink.py`). Pen input:
  coalesced sample timing, pressure and lift endpoints in the uploaded
  file, prediction, palm suppression and palm-first pen takeover, cleanup
  after `pointercancel` / lost capture. Chromium's native touch and pen,
  and synthetic Pencil events for Safari's handler order. Stylus latency
  and OS palm rejection still need a real tablet.
- `inkEditing.mjs`: tap-to-select and the selection menu. Colour/width
  edits keeping pressure and time, duplicate ids, selective delete, the
  Undo/Redo buttons, a finger lasso-move across groups in pen-only mode,
  swipe/hold arbitration, a pen resuming through a selection, menu
  placement on a small screen, view/edit shares. Native Chromium touch/pen,
  asserting on the persisted stroke files. `--only "ink edit:"`; `--only
  ink` runs both files.
- `textBoxes.mjs`: text boxes ([text_boxes.md](text_boxes.md)) on a PDF
  page and on a sheet in both views. It covers the Text tool's click and
  drag, typing, the stored block after a reload, the notes marker's jump,
  and the box's and the row's editors never open at once. An empty box goes
  without an undo entry. Select, move, nudge, the width handle, the menu's
  restyle, Duplicate and Delete each undo. Another client's edit shows
  unclipped and is never measured back, and Ctrl+Z follows the armed tool.
  Then come a finger's tap, drag, scroll and pinch, a stylus on the handle,
  band and editor, a box over a PDF link, a stale `pdf_page` under a sheet,
  and two people on one box. `--group textbox`. Chromium's touch emulation
  shapes the touch step:
  - after a touch scroll dispatched through CDP, taps produce no click,
    even seconds later, so the step taps before it scrolls;
  - a touch pointer is captured by the element under the finger, so taking
    the capture on another element fires `lostpointercapture` on that
    child, and the box's handler checks which element lost it.
- `guide.mjs`: the first-run guide ([onboarding.md](onboarding.md)),
  started from the account menu's Tours — every registered home-view anchor
  is present once, the demo step adds a paper by itself (pointed at an
  uploaded PDF through `gamma-guide-vars`, so no network), the user's
  highlight checks the next step off, finishing records "done", the menu
  restarts it, and Esc leaves a replay. `contextualGuide.mjs` (the AI chat
  tour) and `triggeredGuide.mjs` (offers and hints): onboarding.md "Files".
- `ipad.mjs`: the installed web app ([ipad.md](ipad.md)) — the manifest
  and its icons, `theme-color` following the theme, the standalone-mode
  block in the bundled stylesheet (`display-mode` cannot be emulated in
  Chromium), and a note editor's editing bar by tap (none with a mouse).
  `--only ipad`.
- `pdfTouch.mjs`: 400% rendering under an emulated canvas limit, distant-page
  release/repaint, live ink, native touch swipes ([pdf_loading.md](pdf_loading.md)).
  `--only "pdf touch"`.
- `pdfload.mjs`: PDF loading, in a non-default workspace — the timing probe
  (a 300-page, 20 MB document opened cold at an emulated 20 Mbps, the
  IndexedDB backfill, a warm reopen, a same-tab return; reports the per-phase
  `performance.mark("pdf-<phase>")` stamps and the bytes on the wire as each
  step's note, asserts only that it paints), then the behaviours: page boxes
  from the manifest (a landscape page below the fold), the last-read page
  after a reload, two large documents through the parsed-document cache, the
  anonymous share view by ranges ([pdf_loading.md](pdf_loading.md)).
  `npm run e2e -- --only "pdf load"`.
- `files.mjs`: files dropped on a block row / the page body become file
  chips (a `dropFiles` helper builds a real DataTransfer; the paste step
  builds a `ClipboardEvent` in the page, since Playwright's `dispatchEvent`
  cannot), a PDF chip's right-click "Add to library" makes the document page
  in the project's folder and the chip gets an open-page button, a markdown
  chip's "Add to library" imports a note page and leaves the file untouched,
  the upload endpoint's lab-file / executable rule, and a slow upload
  stopped from Background tasks.
- `collab.mjs`: two accounts in a shared workspace: presence, live ops, edits
  to different blocks, same-block last-writer-wins, undo after a remote edit,
  rename propagation, edits made offline replaying, remote delete, a
  highlight made by the other person.
- `auth.mjs`: refusing an inaccessible explicit workspace without opening a
  different library, the login page, guest login (a new throwaway account
  each time) and demo mode ([guests.md](guests.md)).
- `i18n.mjs`: Settings → Appearance → Language switches the interface to
  Chinese and back through the reload ([i18n.md](i18n.md)). `--only i18n`.
- `share.mjs`: the share dialog, the anonymous share view (PDF, highlight,
  image through the share token, no editor), an edit share.

Every step also asserts that no API call failed (4xx/5xx), no console error
and no page error happened meanwhile (`openPage` records them;
`EXPECTED_FAILURES` in the harness lists designed refusals such as the
metadata fetch's 404 for a PDF without identifiers). Wait for the state a
step needs with `until()` / `waitForFunction` / `waitForEvent`, never a fixed
`sleep` — the one left (the view-only share's "no editor opens") is a
negative check with nothing to wait for. New UI work touching the
save path, workspaces, auth or rendering of URLs should add a step here; the
`/verify` skill runs this suite.

## Debugging surfaces

- **Server log** — Settings → Server → "Server log" (admin only): the
  in-memory ring buffer behind `GET /api/admin/logs`, filterable to
  warnings / errors; the Dashboard above it counts them since startup and
  shows the build and the update check. Backend code must log through
  `gamma/logbuf.py`'s `log` (never `print()`); use `log.warning` for what an
  admin should notice. Secrets are masked at insert time. Gone on restart.
- **Two request lines, on purpose.** The console carries uvicorn's access
  log for every request (`INFO: 127.0.0.1:… "GET /api/… HTTP/1.1" 200 OK`)
  and, interleaved with it, Gamma's own line for the ones worth reading
  (`auth.py` `_finish_request_log`): `[http] request=<id> GET /api/… status=…
  duration_ms=… session=… expected=… reason=…`. It is emitted only for a
  4xx/5xx, a request on an auth path, or one that took over 2 s, so a quiet
  log means nothing failed and nothing was slow. `reason` says which kind it
  was: `authentication-required` (401), `forbidden` (403), `not-found` (404 —
  nothing was rejected, so a client polling something the server let go
  reads as routine), `server-error`, `session-operation`, `slow-request`,
  `bad-token`, `session-mismatch`, or `request-rejected` for the rest.
  `request=<id>` is the `X-Gamma-Request-ID` header the answer carried, so a
  browser failure and its server line can be matched up.
- **Session log + debug tracing** — Settings → Help & diagnostics: browser-side event
  log; the "Debug logging" toggle traces reading-position/restore/sync
  events into it and the console. Every PDF load phase lands here as
  `pdf <phase> +<ms>` (ms since the viewer started opening that url) and as
  a `performance.mark("pdf-<phase>")` for devtools' Performance panel — the
  phases and what a healthy open looks like: [pdf_loading.md](pdf_loading.md).
- **Background tasks** — the topbar's tray lists the server's jobs
  (exports, backups, restores, imports, the search indexer; `GET /api/jobs`)
  and this tab's own work (downloads, uploads, metadata / citation / title /
  translation AI lookups) ([tasks.md](tasks.md)). While a task runs, its
  row shows the progress (what it counts, the phase, the item at hand), the
  elapsed time and a stop button when it can stop. Once it ends, the row
  shows its file, error or finish time, with Download, Start again and
  Remove. A server job's failure is its `error`, and the server log names
  it (`[jobs] <kind> <id> failed`, with a traceback when it is a bug). The
  client polls every 1.5 s while a job runs or the tray is open, else every
  30 s, and not at all while the tab is hidden. Anything that starts
  indexing calls `wakeTasks`, so the row appears at once. A stopped local
  row reads "stopped" and ignores the work's own late reports.
- **Status bar** — Settings → Appearance turns the floating status pill into a
  persistent bar under the tabs.
- **Report a problem** — account menu → "Report a problem…", or the Help
  section of Settings → Help & diagnostics. `src/support/ReportProblem.jsx` asks
  what happened and how to reproduce it; `src/support/problemReport.js`
  (pure, tested by `tests/problemReport.test.mjs`) builds the report. It
  carries the build (`build` on `GET /api/session`,
  `version.build_info()`), the browser and screen, the kind of view and the
  workspace's kind and role (never its name), and the session log's
  warnings, errors and newest lines. An admin's report adds the Server
  dashboard line and the server log's last warnings. Every line goes
  through a JS mirror of `logbuf.scrub`. "Open GitHub issue" copies the
  report to the clipboard and opens `.github/ISSUE_TEMPLATE/bug_report.yml`
  prefilled through its field ids (`description`, `steps`, `diagnostics`),
  trimmed to GitHub's URL budget; keep the ids and the query parameters in
  step. Nothing leaves the browser until the reporter submits the form;
  "Copy report" serves people without GitHub.
  - **Screen recording**: the dialog's Record… row, where
    `getDisplayMedia` + `MediaRecorder` exist. The dialog folds into a pill
    until Stop (the pill, the browser's stop-sharing bar, or the 3-minute
    cap), then returns with the file: webm, or mp4 where the browser
    records that, 1.5 Mbit/s, no sound. A URL cannot carry a file, so Save
    (or opening the form) downloads it, the steps name it, and the reporter
    drops it into the form. The e2e step stubs the picker with a canvas
    stream, so the recorder itself runs.
- **Library health** — Settings → Maintenance lists, per paper:
  metadata state, extracted-text chars, and search-index coverage, with
  per-row retry/reindex buttons plus batch actions: Fetch needed / Refetch
  all for metadata, and Reindex needed (only papers the index is missing,
  holds stale, or hasn't visited — a targeted `/api/search-reindex` with
  `doc_ids`, unlike the Index section's full Rebuild).

## Gotchas worth knowing

- Every guest login makes a new throwaway account, deleted with its
  workspace `guest_ttl_hours` (default 24) later or on logout
  ([guests.md](guests.md)) — don't park test data there. In backend tests
  the `guest` fixture's account name is `conftest.guest_name()`.
- Every endpoint that touches a database or files, and every slow one
  (downloads, AI calls, PyPDF2), is a **sync `def`**, so FastAPI's
  threadpool runs it. One uvicorn process serves every request and page
  socket from one event loop, and SQLite calls block: an `async def`
  endpoint waiting on a workspace's write lock (`db.BUSY_TIMEOUT_S`, 10 s)
  holds up everything else. `async def` is only for handlers that must
  await something (a request body stream, the page socket, the MCP SDK's
  OAuth handlers, the upload of an ink file); they hand their database work
  to `run_in_threadpool`. The session middleware reads users.db through
  `auth._off_loop`, a worker thread with its own token count, so a pool
  full of slow requests never delays learning who is asking.
  `tests/test_event_loop.py` fails on an async route handler that awaits
  nothing, checks that the session read, an op batch and the socket
  handshake run off the loop, and times a `GET /api/session` while a write
  lock is held for two seconds.
- Those endpoints run side by side, so a check followed by a write must be
  one step: take the write lock before the check (`BEGIN IMMEDIATE`,
  `blocks_store.write_lock`) or let a constraint decide (`INSERT OR
  IGNORE`, `ON CONFLICT`). `tests/test_concurrent_writes.py` starts
  several requests on a barrier (a page shared twice, one page id created
  twice, two owners demoting each other, op batches, cover snapshots).
  Module-level caches that threadpool code touches carry a lock.
- The pool also runs streamed replies and file responses, so the app raises
  it from AnyIO's 40 threads to `app.THREAD_TOKENS` (100) at startup: at
  40, 45 slow PDF-proxy downloads in flight made a PDF range read time out.
  Each account's AI calls open at once are capped too ([ai.md](ai.md)
  "Calls open at once").
- A connection outlives its `with` block, in a cache kept per thread and
  database file: the `db.connect_*` helpers return `db.Connection`, whose
  `__exit__` commits (or rolls back), closes the block's cursors and hands
  the connection back ([user_db.md](user_db.md) "Connections"). Never use a
  connection or a cursor after its `with` block, and close a raw
  `sqlite3.connect` with `contextlib.closing`. On Windows an open handle
  keeps a workspace's directory on disk, so code that removes, moves or
  replaces a database file first calls `db.close_workspace_connections(ws)`
  (`workspaces.remove_files` does). `tests/test_db_connections.py` checks,
  with the garbage collector off, that requests leave nothing open outside
  the cache, that the number open stays bounded, and that a deleted
  workspace leaves no handle behind.
- All state is SQLite + files under the data dir (`GAMMA_DATA_DIR`, default
  the repo's `data/`): global `users.db` (accounts, workspaces, memberships,
  shares, account-wide prefs), per-workspace `workspaces/<id>/pages.db`,
  `data.db`, `uploads/`. Safe to inspect with any SQLite client while the
  server runs; on Windows, open handles lock the directory (matters for the
  migration's moves — stop the server before `manage.py migrate`).
- The server upgrades the data directory at startup (`gamma/migrations.py`,
  snapshot first, log line `[migrate]`). A directory written by a newer
  Gamma is not served: every address shows the guidance page instead
  ([migrations.md](migrations.md) rule 3). A workspace's own steps run
  when it is first opened after the upgrade, or in the background walk
  after startup. A request that opens it waits for them, and one whose
  step failed answers a 503 `workspace_not_upgradable` with the guidance
  while every other workspace is served. `python manage.py
  migrate --status` says where a directory stands, with how many
  workspaces are behind.
- Every API call names its workspace (`X-Gamma-Workspace` header from the
  fetch wrapper, `?ws=` on the websocket and in URLs); a 403 "not a member"
  on an otherwise fine request means the tab's workspace is not the one you
  expect — the id is in the URL.
- An AI reply ending in "lost the connection to the server (network
  error)" means the browser→Gamma connection was cut mid-stream, not that the
  provider failed (that comes back in-band as "AI call failed: …"). The
  streams send a keepalive line every 15 s of silence ([ai.md](ai.md)) and
  the server logs `client closed the stream after Ns`; if it still happens,
  a proxy in front of Gamma is closing idle responses sooner than that.
- The docks use `react-resizable-panels` v2, pinned on purpose: v4 changed
  the API incompatibly. Do not let a dependency refresh move it.
- Timestamps are UTC ISO strings with `Z` (`page_now()`); keep the format.
  `db.format_stamp` writes a datetime in it and `db.parse_stamp` reads one
  back (None when unreadable — whether that means expired, due or now is
  the caller's decision).
