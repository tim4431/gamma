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

The Docker image runs Python 3.12 and the iPad and Android apps 3.13, and CI
runs the suite on both: 3.12 with the server's pins, 3.13 with the apps'
(`requirements-mobile.txt`). A venv on a newer Python may accept what they
refuse. `tests/test_python_versions.py`
fails on either kind locally: syntax past 3.11, and an annotation that does
not evaluate (from 3.14 annotations are evaluated only when asked for, before
it as each function is defined, so a class whose method is named `int` stops
its module importing at a later method's `-> int | None`; such a module takes
`from __future__ import annotations`).

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
rename-user, delete-user, list-users, create-workspace, list-workspaces,
set-member, set-access, link-identity, list-identities, unlink-identity,
sweep-guests, hosted-sync, migrate, backups, offsite, litestream-config).

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
  It encrypts the shared AI entries' keys and sign-ins (an account's own
  are stored unencrypted), the Gamma Cloud grants, the mirrors' tokens, the
  publisher sessions and the off-site secret key in users.db, so keep it
  somewhere else, or a restored server cannot read those (the secret key
  can be typed in again).

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
- Any frontend change: run `npm run lint` from `frontend/`. It reads the
  whole tree in under a second ([Frontend lint](#frontend-lint)).
- A change to `frontend/package-lock.json`: the brand ledger,
  `design/brand/generated.json`, hashes the lockfile as part of the rendering
  toolchain, so any dependency change fails check.yml's `branding` job until
  the ledger records the new hash. From the repository root, run
  `node tools/branding/build.mjs` to record it (its requirements are in
  [tools/branding](../../tools/branding/README.md)), then
  `node tools/branding/build.mjs --check` to verify it. While Playwright's
  version stays the same, that hash is the only line that should move. If the
  run also re-renders images, because this machine's system fonts differ from
  the last renderer's, restore them with `git checkout` and keep only the
  ledger's lockfile line ([design/brand](../../design/brand/README.md)).
- UI behavior changes: build once after the final frontend edit so the suite
  sees current code, then `npm run e2e -- --changed`, which runs the groups
  the working tree's changes select (see [the browser suite](#browser-end-to-end-suite);
  `--changed main --list` shows a branch's selection without running it).
  Narrow further with `--group` or `--only` when the selection is broader
  than the change, e.g. a one-line edit in a shared module selects every
  group. Check that the intended steps actually ran. A pure-module change
  does not automatically require a build or browser run.
- Leave the developer's screen and keyboard alone. One switch decides for every
  suite, `tools/headed.cjs`: the browser suites and the extension's run headless
  and the desktop scripts keep their window minimized, unless a run passes
  `--headed` or the shell sets `GAMMA_TEST_HEADED=1`. A new suite reads it rather
  than deciding for itself (`desktop/test/headed.test.js` checks that).
  `tools/readme-media` and the other media tools are always headless, and an
  Android emulator takes `-no-window`; a one-off script does the same
  ([the survey](../research/test-windows.md)). `--headed` and `npm start` are
  for when the developer asks, or for a measurement that needs a real display.
- The iPad and Android apps show the web app, so a web change needs no
  device check of its own. A change to the host bridge
  (`shared/lib/host.js`, `shared/lib/saveFile.js`, the ink layer's claim)
  runs the `ipad` and `android` browser groups; one that changes a message
  changes the Swift and the Kotlin sides with it ([ipad.md](ipad.md)
  "The host contract", [android.md](android.md)). The Android app's own
  code has tests of its own ([below](#the-android-apps-tests)).
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
pip install -r requirements-dev.txt   # pytest, pytest-xdist, pytest-timeout, httpx, moto, requirements-s3.txt
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
throwaway data directory (`gamma-test-…` under the OS temp dir, removed when
the process exits), and `--dist loadfile` keeps each file's tests on
one worker in file order (tests inside a file may build on each other;
files never may). The shared `client` fixture carries the cookie of the last
login on that worker, so a "not signed in" check uses the `anon` fixture (a
fresh client), never `client`. Every client `conftest.py` hands out
(`client`, `guest`, `anon`, `login()`, `fresh_client()`) runs on the session
client's event loop, the one loop a uvicorn process has. A bare
`TestClient(app)` gets a loop per request and per socket, and a room's
fan-out sent from one socket's loop to a reader already waiting on another
wakes no one: the read never returns. That hang held the CI job for hours
at a time; `pytest.ini` now ends a test after 300 s with every thread's
stack (pytest-timeout), xdist reports it as crashed and runs the rest on a
fresh worker, and the job itself is capped at 20 minutes. One data directory serves every file on a
worker, so an account name belongs to the module that creates it: prefix
names with the module's area (`bk_admin`, `ca_alice`), create them through
`conftest.make_user`, and pick folder names no other module uses in the
`guest` fixture's workspace. That fixture's account is
`conftest.guest_name()`, a fresh `guest-…` name per run; never write
`"guest"`. `make_user` fails the run when a second module asks for a name
another module already created. The upload GC's queue is shared the same
way: the names a batch dropped wait `DEBOUNCE_S` in their workspace's queue
for the module's thread, so a test that checks what `upload_gc.flush`
returns for the `guest` workspace flushes it first, or it also gets the
names another file dropped a moment before (`test_ink.py`'s ink files),
and sets `DEBOUNCE_S` high, so the thread leaves its own names to that
flush (as `test_upload_gc.py`'s `gc` fixture does).
Run them with the project venv's interpreter (`venv/Scripts/python.exe` on
Windows): the two vector-math tests need `ziamath` from `requirements.txt`,
and a system/conda `python` without it fails them with "ziamath is not
importable" rather than a puzzling path count. A test that reads a child
Python's output (`manage.py` in a subprocess) decodes it as UTF-8 and sets
`PYTHONIOENCODING=utf-8` in the child's environment: `text=True` decodes
with the locale's codec (cp936 on a Chinese Windows), and a child that
inherited `PYTHONIOENCODING=utf-8` writes UTF-8, so a "→" in its output
fails the read.

`conftest.py` also holds what several files share: the `data_dir` fixture
(a data directory of the test's own, for the migration, backup and startup
tests that must not touch the worker's shared one; the upload GC's timed
full pass is off for the session, since it would open whichever test's
databases are current a minute after import), `at_once` / `together`
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

The Zotero link's tests ([zotero_sync.md](zotero_sync.md#tests)) never
reach zotero.org: they run against an in-memory Zotero,
`tests/zotero_fake.py`'s `FakeZotero` (the Web API, zotero.org's OAuth
steps and the file storage, with Zotero's versions and refusals), which
`install(monkeypatch)` points the server at, serving
`tests/zotero_fixtures.py`'s `standard_library`. `conftest.py` drops every
`GAMMA_ZOTERO_*` variable at import, so a developer's stand-in address or
Zotero app never reaches a test. The engine's suites,
`test_zotero_sync*.py` (the registry and the API, folders, items, PDFs,
the first link, annotations, notes, large libraries, hardening, crashes,
the fuzz), share one harness, `tests/zotero_engine.py`, loaded as
`ai_fixtures.py` is: each file imports the fixtures it uses by name
(`# noqa: F401  (fixtures)`) and keeps its own `USER` and `PASSWORD`, which
`owner_id` and `me` read; never through `pytest_plugins`, which would
reach every test of the session with its autouse fixtures (`background`,
which records the background rounds asked for instead of running them, and
`no_relocation`). Rounds run when a test asks, with the budget lifted.
`me` checks the owner's links before it removes them: one more round
writes nothing on either side (`tests/zotero_idle.py`; a test that leaves
a link unsettled on purpose says so with `idle.skip(reason)`). The fuzz
runs 8 seeds by default: `ZOTERO_FUZZ_SEEDS=2000 ZOTERO_FUZZ_STEPS=100`
runs more, and `ZOTERO_FUZZ_TRACE=<key>` follows one object round by round.

Rules the frontend mirrors — search normalization (`gamma/textnorm.py` ↔
`frontend/src/shared/lib/textnorm.js`), published pages' slugs and page
hosts (`gamma/publish.py` ↔ `frontend/src/shared/lib/slug.js`), text boxes
(`gamma/text_box.py` ↔ `frontend/src/markup/textBox.js`,
`tests/shared/textbox.json`, [text_boxes.md](text_boxes.md)), pictures
(`gamma/picture.py` ↔ `frontend/src/markup/picture.js`: the normalizer,
the turned frame, the Markdown line and the merge, `tests/shared/picture.json`
and `picturemerge.json`, [pictures.md](pictures.md)) and
handwriting (`gamma/ink.py` ↔ `frontend/src/ink/ink.js` and
`ink/grain.js`: the codec, dashes, merge, the calligraphy nib and the
pencil's grain, [handwriting.md](handwriting.md)) — are pinned by ONE
set of cases both sides read: `tests/shared/*.json` at the repository root,
run by `backend/tests/test_shared_fixtures.py` and the matching node tests.
So are a block's kind (`blockkind.json`: the `kind` column and
`blocks_store.block_kind` ↔ `editor/blockProps.js`), item types
(`itemtypes.json`: `gamma/item_types.py` ↔ `shared/lib/itemTypes.js`),
colours (`colors.json`: `gamma/colors.py` ↔ `shared/model/palette.js`),
share-link names (`linkname.json`: `auth.link_name` ↔
`collaboration/linkName.js`), person colours (`personcolor.json`:
`pdf_cards.person_color` ↔ `collaboration/personColor.js`), the chat's
passage budget (`passages.json`: `ai_context.fair_shares` ↔
`chat/passageBudget.js`), what a page carries (`attachment.json`:
`blocks_store.page_attachment` ↔ `library/libraryUtils.js`), sibling keys
(`fractionalkeys.json`: the npm and PyPI fractional-indexing libraries) and
pasted math (`pastemath.json`: `gamma/paste_math.py` ↔
`editor/pasteMath.js`). The kinds are run against the column by
`test_block_columns.py`, the pasted math by `test_paste_math.py` and the
slugs by `test_publish.py`. Add a case there when a rule changes; whichever
side drifts fails. `test_every_fixture_is_read_by_both_suites` fails a
fixture that no node test or no pytest module reads, except those in its
`ONE_SIDED` table: the mirror's tree rules and the text merge, which only
the server runs, and the held pen shape, which the server never draws. It
counts a name only as a string a test opens (closed by a quote), so a
comment that still names a fixture after its reader went does not hide the
gap.

A twin drifts where the two languages differ, so a new fixture should try
those inputs. JS `.length` and `slice` count UTF-16 units where Python
counts code points (an emoji, a math letter such as U+1D465). `Math.round`
takes a half up where Python's `round` takes the even one (`ink.js_round`,
`text_box._round`). JS `\s` and `trim()` take U+FEFF and leave U+0085, and
Python's `\s` and `strip()` the reverse. A JS object literal answers its
prototype's keys (`"constructor"`), where a Python dict does not. And a
library on each side may check its inputs differently: neither
fractional-indexing checks a key's characters, so `fractionalkeys.json`'s
`foreign` keys are the ones the server refuses itself.

The frontend has few component tests (its lint is [below](#frontend-lint)). Its pure modules have
`node --test` tests (`npm test` from `frontend/`, the files in
`frontend/tests/*.test.mjs`): `blockOps` (diff/apply), `collabSession` (the
page session's transport logic over fakes — ordering, reconciliation,
retries, presence, the caret throttle), `sessionState`, `settings`
(navigation, presets), `textnorm` and `libraryUtils` (the shared cases
above), `mdMarks` (the formatting hotkeys' toggle), `logseqPdfModel` (tree
ops), `blockHistory` (the undo classifier), `menuAim` (the safe-triangle
geometry), `themes` (the design tokens: the desktop copy, the pre-paint's
theme lists, every theme resolving, the contrast ratchet),
`designTokens` (the raw-value ratchet on the stylesheets, and no
`(pointer: coarse)` rule sizing a control but the text box's handle), and
`inert` (no string given to the `inert` prop in `src`, and React writing
the attribute for true and removing it for false).
Both ratchets are described in [ui-design.md](ui-design.md#ratchets). A
module is testable there when its relative imports carry the `.js`
extension (node resolves nothing else). Modules that import React can still be imported for their
pure exports. A few tests render React itself. The markdown ones
(`callouts`, `noteSanitize`, `diagramMarkdown`, `remarkPaperLinks`,
`textBoxShown`) render to static markup, and `leave` and `inert` mount a
React root in jsdom. Node runs React's development build, so StrictMode's
double ref attach shows there, as it does in `npm run dev`. Other React
rendering and interactions are exercised by the browser suite below, plus one standalone
browser regression: `npm run e2e:latex` bundles the block
editor with esbuild over an in-memory fixture ([latex_editing.md](latex_editing.md)).

### What a run leaves behind

Nothing when it ends normally, and nothing stays up when it does not.

Every pytest process of the server's and the account server's suites owns one
folder in the system temp directory (`tests/testrun.py`, which both
`conftest.py` files import first): `gamma-test-*` or `gammacloud-test-*`, holding
`data/`, the data directory, and `tmp/`, where `tempfile` points, so import
staging, export spools and pytest's `tmp_path` land there too. The folder goes
when the process exits, a failed test's `tmp_path` with it; pass
`--basetemp=<dir>` to keep those. A killed process cannot remove its folder, so
the next run removes any more than a day old.

A run also ends itself when it stops getting anywhere. The timeout in
`pytest.ini` bounds a test and nothing else, and a run has stuck between tests:
an xdist controller waiting for a worker that was waiting for the controller
sat for five days with every process idle
([research/test-temp-files.md](../research/test-temp-files.md#the-run-that-stalled)).
With no test finished for 15 minutes a controller, or a run without workers,
exits with status 1 and its workers go as their channel closes. So a run that
stops at status 1 with no summary stalled: every thread's stack is in
`stalled.log` in its folder, which stays for that day. The stacks go to a file
because a full stderr pipe would stop the dump and the exit after it. A worker
cannot judge a stall, being idle while the others finish, so it only has a
two-hour limit, for one too stuck to notice its channel closing. A pause in
`pdb` or at a `breakpoint()` stops the clock; an IDE's debugger paused that long
ends the run.

The browser suite follows the same rules with what it has. A run that passes
removes its `gamma-e2e-*` folders, and one that fails keeps them without the
copy of the build, for three days: `run.mjs` removes older ones when it starts.
A process with no step finished for 15 minutes ends with status 1 (not under
`--headed`). Workers leave when their parent is gone, and a backend reads a
stdin pipe only the process that started it holds, so killing a run takes its
workers, backends and browsers with it.

### Frontend lint

```bash
cd frontend
npm run lint    # Biome over src/, tests/, tools/ and vite.config.js, under a second
```

`npm run lint` runs `biome lint --error-on-warnings` with
`frontend/biome.json`. It reads the scripts, the stylesheets and the i18n
catalogs, so a duplicate CSS property or JSON key fails it too. Every
diagnostic fails the run, and check.yml runs it before the unit tests.
Biome is pinned to one exact version in package.json, because a release can
change what a rule reports. An upgrade is a change of its own, made with a
lint run.

Only rules that find bugs are on. There is no formatter and no style rule.
The config takes Biome's recommended `correctness` and `suspicious` rules
and adds four that those presets leave out:
- `useHookAtTopLevel`: a hook after an early return or inside a condition.
  That is how a blocked share link once crashed to a blank page.
- `noUndeclaredVariables`: a name that is not imported or declared.
- `useJsxKeyInIterable`: a mapped element without a `key`.
- `noNestedComponentDefinitions`: a component defined inside another one,
  which remounts on every render.

`noUnusedVariables` is raised to an error. These recommended rules are off,
each for a reason:
- `noUnusedImports`: 34 files still carry an unused `import React` from
  the classic JSX runtime. The rule can go on once they are removed by hand.
- `noUnusedFunctionParameters`: a callback names its positional parameters
  to reach the ones after them.
- `useExhaustiveDependencies`: the partial dependency lists are deliberate,
  and the old `eslint-disable` comments mark them. The config names it off,
  so a change of preset or a Biome upgrade cannot turn it on.
- `noAssignInExpressions`: `exec` loops and parenthesised lazy
  initialisation are written that way on purpose.
- `noShadowRestrictedNames`: key handlers are called `escape`.
- `noThenProperty`: openBlock's `then:` option and settingsReads.js's
  `then:` steps are never awaited.
- `noPrototypeBuiltins`: it flags the safe
  `Object.prototype.hasOwnProperty.call` form too.
- `noGlobalIsNan`: `isNaN(date)` is meant. The `Number.isNaN` fix is always
  false for a Date.
- `noApproximativeNumericConstant`: a test writes an A4 page's aspect as
  1.414 on purpose.
- `useParseIntRadix`: `parseInt` reads decimal data attributes, which need
  no radix.

`useIterableCallbackReturn` runs with `checkForEach: false`. It checks that
a `map` or `filter` callback returns a value. It does not check that a
`forEach` arrow returns nothing.

A diagnostic is fixed by hand, never with `biome lint --write` (its
unused-import fix once took a file's header comment with it). Where the code
is right as it is:
- Suppress the one line with `// biome-ignore lint/<group>/<rule>: <reason>`
  above it (in CSS, `/* biome-ignore … */`). A suppression that no longer
  suppresses anything fails the lint too.
- Prefix a binding that must stay unread with `_`. It is not reported.
- `x ||= v` on a variable read nowhere else is reported unused. Write
  `if (!x) x = v`.
- A page global read inside a scenario's `page.evaluate` is written
  `window.x`, since the lint knows only the ES, browser and Node globals.

### Browser end-to-end suite

```bash
cd frontend
npm run build                   # the suite drives frontend/dist
npm run e2e                     # parallel workers, ~2 min on 6; exit 1 on any failure
npm run e2e -- --jobs 1         # one worker, groups in order, live output (~8 min)
npm run e2e -- --changed        # only the groups the working tree's changes select
npm run e2e -- --changed main --list  # a branch's selection and why, without running
npm run e2e -- --group ink,collab     # these groups (ids: `--list`)
npm run e2e -- --shard 1/2      # half the groups by time (CI runs each half on its own runner)
npm run e2e -- --only collab    # steps whose name contains "collab" (one worker)
npm run e2e -- --continue       # keep going after a failure
npm run e2e -- --headed         # watch the browser
npm run e2e -- --keep           # keep the temp data dir + server.log
```

High-zoom tablet regressions: `npm run e2e -- --only "pdf touch" --keep`.
A context without `hasTouch` reports no touch points whatever the host has
(the harness pins `maxTouchPoints` to 0). Chromium passes the system's touch
digitizer through, and a Remote Desktop session from a touch device brings
the client's ten points, which turn on the touch-only controls and guide
steps.
#### WebKit and Firefox

`GAMMA_E2E_BROWSER=webkit` or `GAMMA_E2E_BROWSER=firefox` (after
`npx playwright install webkit` or `firefox`) runs the suite in that engine.
Native touch gestures need Chromium's CDP, so there the touch scenario checks
only the 400% PDF/ink paint and bitmap release at tablet dimensions and DPR 2.
That is browser emulation, not an iPad measurement. Both engines skip the
steps gated on `browser.browserType().name() === "chromium"`. The other steps
that open a CDP session fail in both. So does every context that asks for the
clipboard permissions: WebKit lacks `clipboard-write`, Firefox both.
Neither engine passes the whole suite. The `touch` and `ipad` groups pass
under WebKit, and the iPad app is a WKWebView, so that is the run to make for
a change to touch editing. It is still no iPad:

- Playwright's WebKit is not the iOS port, and its tap is a patch to the
  engine, not a finger. It sends `touchstart` and `touchend`, and the mouse
  events and the click only if neither was handled, a cancelled
  `pointerdown` counting as handled. So a tap on the editing bar or a window
  grip, which cancel `pointerdown` and run from the click, did nothing there.
  WebKit's iOS test
  `pointerevents/ios/pointer-events-prevent-default-allows-click-event.html`
  requires the click, so the gap is Playwright's.
- `tests/e2e/webkit.mjs` puts it back for a `hasTouch` context: the one
  click, from inside `touchend`, when `pointerdown` was cancelled and neither
  touch event was. It also reports five touch points where this build reports
  none, which turns on CodeMirror's `browser.ios`. A run prints this at the
  start, and the first `touch` step asserts that Playwright's own tap still
  sends no click, so an upgrade that fixes it fails there. A pass shows what
  the app does with that click, not that an iPad sends it
  ([device check 37](ipad.md#device-checks)).
- No on-screen keyboard or real visual viewport comes with it, `page.keyboard`
  is a hardware key, and this build reports a Mac user agent and platform
  Win32 unless a context sets one.
- A step that cannot check something there says so in its result line. This
  build has no `overscroll-behavior`, and with no `clipboard-write` the PDF
  popup's Copy runs but its text is not read back.
- WebKit rejects the fetches a page still has open as it navigates, in the
  words of a CORS failure, and they show as unhandled rejections on the page
  being left. Logged against a run's navigations, each fell between the
  main-frame navigation request and its commit, beside Playwright's `Load
  request cancelled`. `cancelledLoad` lets that message pass for the page's
  own origin, which cannot fail an access control check, and no other.

`GAMMA_E2E_BROWSER=webkit npm run e2e -- --group touch,ipad --continue` runs
them; `--only` skips the setup steps, so its failures prove nothing here.

#### The tab-close scenario


`node tests/e2e/tabClose.mjs` is a scratch scenario apart from the groups. A
note is typed, a stroke drawn or a setting changed, and `page.close()` follows
at once, in the engine `GAMMA_E2E_BROWSER` names. Each step asserts that the
server has the change or, for a stroke, that the draft journal brings it back
on the next visit. `--runs N` repeats each case, `--close page,context` adds
`context.close()`, and a table counts the outcomes. `--stop pagehide` keeps
every `pagehide` listener the app adds from running, as Firefox stops a
closing page's first one. Every `page.close()` case passes in Chromium,
WebKit and Firefox, with or without `--stop pagehide`.
`context.close()` loses the changes in Chromium's headless shell, which
runs no listener there, and in Firefox, which sends without the session
cookie
([research/keepalive-abort.md](../research/keepalive-abort.md#the-app-at-a-close)).
A step that times a close passes `{lastLook: false}` to `page.close` or
`context.close`, which skips the screenshot the harness takes first.

`node tests/e2e/collabSoak.mjs` is the crowd soak for real-time
collaboration ([collab.md](collab.md#testing)), a script apart from the
browser. It needs no build: its server serves the API alone
(`new Server({ static: false })`). Twenty tabs, each the app's page
session over Node's fetch and WebSocket, type into 5 pages of a shared
workspace for 120 s, then go quiet until every tab shows the server's
tree. `--clients N`, `--accounts N` (default one per tab), `--pages N`,
`--seconds N`, `--seed N` and `--keep-live N` (the tokens a tab keeps
before it takes its oldest out again, so the pages stop growing) size the
run. `--faults` drops a socket every 4 s and takes a tab's network away
for 4 s every 6 s. It prints a row per 30 s window (batches/s; the ack,
the fan-out to each peer's socket and the time until a peer shows a token
at p50/p95/p99; the server's memory, pages.db + WAL; the driver's
event-loop delay) and the totals, and exits 1 on any failure. `--strict`
also fails a run whose server memory or pages.db grows in its last third,
or whose fan-out p99 in a window passes max(3 × the first full window's,
50 ms); it needs a run of some 300 s to mean anything. `--json out.json`
writes the report. A failed run keeps the temp dir with `forensic.json`:
the report, and for each token lost its batch's base, ours, the stored
text before it, what was stored and the merge. `--sabotage` sends one set
without a base mid-run, which the run must fail on by its batch id (the
oracle's own check). The `collab-soak` group is a 20 s run of 5 tabs as
alice and bob on 2 pages with faults on seed 1, which checks correctness
only. It takes about 25 s and runs in CI's e2e shards like any other
group. On one seed the tabs do the same things each run; only the
server's timing varies.

`frontend/tests/e2e/run.mjs` starts an ISOLATED backend (the project venv's
python — or the interpreter `GAMMA_E2E_PYTHON` names — over a fresh
`GAMMA_DATA_DIR` under the OS temp dir, on a free port, serving a copy of
`frontend/dist` taken at start, so a build during the run cannot break its
page loads, with a `TMPDIR` of its own in that temp dir, so what it stages
there (an import review's upload) goes with it, with the update check, the models.dev catalog and the paper
registry lookups switched off so no step waits on the internet), creates
the accounts `alice` / `bob`, and drives Playwright's Chromium
(`playwright` is a devDependency; the browser is downloaded once on first
launch). A failed step saves a screenshot of every open page plus the
pages' recorded problems and the server log's tail under the temp dir's
`failures/`, and the temp dir is kept without its copy of the build (the
summary prints its path). A page
the step closed on its way out (the usual `finally { await ctx.close() }`)
is there too, as `-closed.jpg`: closing a context or a page during a step
first keeps its last look. The
`check` workflow runs the suite on every PR and uploads those folders as the
`e2e-failures-<shard>` artifact. `harness.mjs` holds the server
lifecycle, `Account` (session cookie + `X-Gamma-Workspace` for API
seeding, browser contexts logged in as that account), `makePdf` (a small
real PDF with a text layer) and `step()`. It also holds two checks of
where a surface opens. `assertOpensInPlace` reads a menu or card frame by
frame: no frame of it where it then moves
([ui-design.md](ui-design.md#menus-and-popups-open-in-place)).
`assertMenuFrom` finds a toolbar button's menu 4 px below or above that
button, by its left edge ([ui-design.md](ui-design.md#menus-and-submenus)).
`assertMenuRoles` reads an open menu's roles: one `role="menu"` around its
items, every control in it an item, and no menu around it but a flyout's
parent. The locator that finds the menu by its name checks the name.
Surfaces arrive and leave with motion
([ui-design.md](ui-design.md#surfaces-come-and-go)), and the suite runs
with it on. `arrived(locator)` waits until a surface has grown in before
its box is read; `assertMenuFrom` does. `live(selector)` leaves out the
copies of closed surfaces on their way out, for a count taken the moment
a menu closes. Role-based locators skip those copies by themselves (they
are `aria-hidden`).

A git worktree has no `backend/venv` of its own, and the harness looks for
the interpreter there, so run the suite in one with `GAMMA_E2E_PYTHON`
naming the main checkout's (`<checkout>/backend/venv/Scripts/python.exe`
on Windows, `bin/python` elsewhere). The group ids are the kebab-case ones
`--list` prints (`sub-pages`, `notes-pdf-share`, `quick-open`), not the
scenario files' names.

The run is split into groups (`GROUPS` in `tests/e2e/select.mjs`, one per
scenario file except `notes-pdf-share`, whose scenarios hand data along;
`run.mjs` maps each id to its scenario functions). `--jobs N` (default:
half the CPUs, at most 6; one with `--only`) forks N workers, each with its
own backend, data dir and browser; a worker takes the next group off the
queue, longest first (each group's `s`, its seconds in a CI run), and the
group's lines print as one block when it ends. A group must not rely on another group's data: put scenarios that do
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
(settings, about two and a half minutes in CI), so split it before adding
workers or shards.

`--shard k/n` runs the k-th of n shares of the selection. Each group,
longest first by its `s`, goes to the share with the least time so far
(`shardOf` in `select.mjs`), so the shares take about as long. CI runs the
two halves (`--shard 1/2`, `--shard 2/2`) on two runners, about three
minutes each. When a group's time drifts far from its `s` (a CI log prints
each group's seconds as `── <group>  (N s`), update it.

A group that throws outside its steps (its own setup: accounts, seeded
pages) runs none of them. The summary counts it as a failure,
`<group> (group aborted)`, and prints the error with its cause.
`server.manage()` runs manage.py asynchronously: a synchronous run of a
few calls in a row (about a second each) blocks Node past uvicorn's 5 s
keep-alive timeout, and the next request goes down a socket the backend
has closed (`fetch failed`, "other side closed", on Linux).

`--changed [ref]` picks the groups from what changed: the working tree
(staged, unstaged, untracked) against `HEAD`, or everything since the branch
left `ref`. `RULES` in `select.mjs` maps each source path (first matching
glob wins) to the groups that exercise it. `frontend/src/guide/**` goes to
the three tour groups. `frontend/src/markup/**` (the page layers and the
tool strip, whose anchors the ink tour uses) goes to the ink groups (ink,
ink-editing, ink-shapes, ink-zoom, ink-ai, pen), pdf-touch, touch, notebooks,
triggered-guide, textbox, pictures and comments (a press on a margin card
with a pen or the Text tool armed). The pictures group also runs for
`frontend/src/ink/`, `notebook/`, `editor/` and `pdf/`, and for
`backend/gamma/picture.py` (with notebooks, the export groups and the
chat's) and `text_box.py`. `backend/gamma/ink.py` goes to ink,
ink-editing, ink-shapes, notebooks, triggered-guide and transfers, and
`backend/gamma/notebook.py` to notebooks and transfers: the Notability
note the transfers scenario imports writes its strokes through the first
and paints its paper page with the second's paper. The chat's
pictures of handwriting (`backend/gamma/ink_view.py`) go to the groups of
the AI modules (`ai_*.py`: the chat's, settings and ink-zoom). The
selection's Redraw as shapes and Convert to text
(`backend/gamma/ink_ai.py`, `backend/gamma/routers/ink_ai.py`) go to
ink-ai alone, which `frontend/src/ink/` and `markup/` run too.
The Android app's page-side scripts (`android/app/src/main/assets/shell/**`),
which the android group runs as they are, and the chrome pages
(`desktop/ui/**`), which it shows under the app's `chrome.js`, go to the
android group; the ink layer, settings, the opened files, `folderZip.js`,
`FileChip.jsx` and the tasks tray select it beside their own groups.
Docs, desktop, extension, the Android app's Kotlin and backend tests go to none (but the fixtures a
scenario imports or serves: the Notability and GoodNotes notes, the fake
Zotero and its library), and what every
scenario goes through
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
CI runs everything, in its two shards.

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
- Typing the moment an editor opens outside an input event (a palette
  command runs from a timer): React runs that commit's effects a task later,
  and the browser handles a key before them. `BlockCmEditor`'s value sync
  took such a first letter out and put it back behind the caret ("before"
  saved as "eforeb") until it skipped its mount run. An effect that undoes
  typing is the app's bug; the step still waits for the editor's focus and
  caret before it types.
- A wait whose condition an earlier state already meets. The chat's Retry
  step waited for two messages with an error kind, which the refused first
  request had already saved. It now waits for the retried answer's
  `overloaded` kind. In the same way the chat offers the generic effort
  levels until `model-info` answers, so the effort step waits with
  `until()` for the model's own levels instead of reading the menu once.

Open: `ink edit: pen resumes writing…` once missed a tap on the stroke on a
loaded 4-core run. Stale layout, a long-press cancel and the undo's
pending delete were each ruled out. When a tap opens no menu, `tapInk`
fails with what was under the tap.

The scenarios live in `tests/e2e/scenarios/`:

- `diagrams.mjs`: Mermaid and SVG diagrams in notes and chat, streaming fences,
  editing, source copying, SVG downloads, theme changes, sanitizing and the
  SVG image's isolation, the width grips on a note mixing both kinds, and
  Markdown round trips. Run with `--group diagrams`; implementation details in
  [diagrams.md](diagrams.md).
- `mentions.mjs`: paper search, keyboard and touch selection, reference limits,
  persistence, PDF receipts and textarea shrink after clearing context. Run with `--only mentions`.
- `noteMentions.mjs`: the note editor's @ menu, its chips, Invite… and a reminder's
  card ([mentions.md](mentions.md)). Run with `--group note-mentions`.
- `comments.mjs`: threads on a note, the page and a highlight from their
  controls, the popover opening in place, live replies, resolve and
  reopen, the Comments window, Ctrl+Shift+M and Escape, edit and delete, a
  viewer's and a link visitor's side, `?thread=` and a folder share's
  thread link, and a page's threads (and a PDF's margin cards) asked for
  alongside its tree. Also the field as a note's editor, the selection popup's
  Comment and the PDF margin's cards ([comments.md](comments.md) "In the
  app"), and the cards beside the pen strip: a pen or the Text tool on a
  card, a handwriting selection let go by a press on one, its right-click,
  and its Enter and Escape with the zoom window open. Run with
  `--group comments`.
- `inbox.mjs`: an invitation's jump to its workspace; the bell's count from
  a page socket's nudge after a mention typed through the @ menu and, on the
  home library, on focus; the jumps in place, into another workspace and
  through a share; Mark all read, Archive and back, an item settling once
  its page is in Recently deleted, and Inbox notifications ringing once per
  browser (a stand-in `Notification`) ([comments.md](comments.md) "The
  Inbox"). Run with `--group inbox`.
- `chatNavigation.mjs`: a library or PDF chat reply keeps streaming and is
  saved while the user navigates away and back, before or after it finishes;
  the model menu lists one connection's models.
  `--only "chat navigation"`.
- `publish.mjs`: publishing a page to Gamma Cloud end to end. A second
  Gamma is started as the share host (`new Server({env})` in `harness.mjs`
  passes `GAMMA_CLOUD_ISSUER`, `GAMMA_CLOUD_POLICY=provision`,
  `GAMMA_CLOUD_SHARE_HOST=1`), and `fakeCloud.mjs` stands in for the
  account server both servers trust: discovery naming the share host, an
  authorization endpoint that answers at once, PKCE code and refresh
  grants, EdDSA ID tokens with their JWKS, `/userinfo`, the profile and
  server-list endpoints, and the username lookup (every `person` is in its
  directory). The account links its identity through the real
  round trip from the share popover, publishes, opens the link on the share
  host anonymously, changes the cloud share's audience, syncs, checks the
  pill and Settings' Publishing row, unpublishes, stops publishing
  from Settings, and publishes a page with a page inside it (the hint
  counting it, the page inside read as published with its parent, Leave
  out keeping it off the share host). `--only publish`.
- `mirror.mjs`: Settings → Workspaces → Clones and Zotero links — the server clones one of
  its own workspaces through the dialog with a write token made via the
  API: Sync, the empty conflicts list, opening the clone, the sync pill's
  log and settings (cadence, detach, reattach), a same-block conflict
  resolved on its row chip, Remove origin ([mirror.md](mirror.md)).
  `--only mirror`.
- `zotero-link.mjs`: a Zotero link on the clone's surfaces. A second Gamma
  talks to the fake Zotero (`backend/tests/zotero_fake.py --port 0`, over
  HTTP, serving `zotero_fixtures.standard_library`) through
  `GAMMA_ZOTERO_API_BASE` / `GAMMA_ZOTERO_WEB_BASE`, and to a stream
  address that refuses connections (`GAMMA_ZOTERO_STREAM_URL`), so nothing
  reaches zotero.org. An account connects with a pasted API key (Account &
  sync → Zotero), links My Library into a new workspace from Workspaces →
  Clones and Zotero links, which closes Settings and opens that workspace,
  its pill showing the first sync from its first read (running or done,
  never "Not synced yet"), then syncs it from its row (a Zotero row, no
  "clone of", tagged open); the pill reads
  `waiting` on the library's linked file until Zotero trashes it, then
  `ok`; the paper has its PDF, highlight, ink group and text box, the
  collections and manual tags are folders and labels. The pill's settings
  turn the cadence to Manual and a round after an edit off, so each round
  is a Sync now the step presses: a highlight's note edited here reaches
  the fake (read back through its API with the key), a colour patched
  there is redrawn here, and a volume changed on both sides is a conflict
  on the pill and on the page's record chip, taken from Zotero. A group
  library linked "When opened" into the open workspace (50 newer items
  added first, so the paper is not among the newest) closes Settings on it,
  gives it its pill without a reload, and shows the PDF card while the
  page's own fetch is held, then draws the PDF and a read-only badge on another member's
  annotation; a paper whose PDF is a linked file takes a PDF attached in
  Gamma, under the card that says why. Detach and reattach from the pill,
  Remove link from Settings
  ([zotero_sync.md](zotero_sync.md)). `--group zotero-link`. Its `RULES`
  route the link's modules (`backend/gamma/zotero_*`, `routers/zotero*.py`,
  the Zotero files under `frontend/src/collaboration/` and `settings/`),
  the fake and its fixtures to it; the Zotero import's note and record
  modules, the clones' surfaces and clock, the PDF viewer, the editor and
  Settings select it beside their own groups.

- `notes.mjs`: New page → title → first block (the seed-block insert),
  Enter / Tab / Shift+Tab / Backspace, Shift+Enter as a line break vs the
  Enter-as-a-line-break preference, Ctrl+Z, the handle menu, todo checkboxes,
  an uploaded image (its URL must carry the workspace), the workspace
  switcher. Runs in a NON-default workspace on purpose.
- `blockProperties.mjs`: the read-only Properties popover
  ([ui-design.md](ui-design.md#the-properties-popover)) on a link region,
  its note, a text box and a picture. It opens from the handle menu under the row, then
  checks the copy buttons and the row menu's key and SQL paths against the
  clipboard, and the JSON view at the same height. A `PUT` from the API
  shows up live, and the scenario steps into the page and Backs out.
  Escape and a press outside close it. It also opens from the highlight
  menu, the tab menu, a library card (the server's whole record, so
  nothing reads as unsaved; Enter and Delete there leave the library's
  selection alone, and a row's "⋯" opens its menu), a folder's menu and
  the palette, on a phone-sized touch screen, and in Chinese. `--group block-properties`.
- `pdf.mjs`: upload + page by attachment, the viewer's text layer, a
  highlight from a text selection (overlay, quote row, persisted position),
  the find bar hitting page 2, an AI citation link
  highlighting its quote on the cited page ([pdf_citations.md](pdf_citations.md)),
  the chat's coverage pills against a mocked `/api/ai/chat` (a selection's
  page and picture, a PDF that went as text and why), the translate button and the selection popup's translator (against a
  mocked `/api/ai/translate`; the selection names its page and does not wait
  for the paper's brief), and "replacing the PDF moves a highlight to its
  text": the job's result, a highlight left hollow (on a flipped page and
  in the notes too), attach mode and move mode ending each other, two
  renumbered citations matched roughly and drawn dashed, their review card
  on hover (the before picture, the changed words, the steps on one line
  and the walk's controls under them, Next with the notes following;
  Accept leaving the card as it was within a pixel, Accepted pressed, then
  going on to the next one to check, pressed again making it dashed again,
  a press within the moment keeping the card, staying while another is hovered,
  a move from it, Ctrl+Z taking the move back to Accepted pressed with the
  count kept and Ctrl+Y bringing Moved back, and that move's undo back to
  Accepted), the hollow one's
  card (the picture of the first file, Previous and Next walking the dashed
  and the hollow ones in document order and round, the accepted one kept
  in the walk and the count with it, Accept disabled until Move… reopens
  it Moved, Accept then "All reviewed", Moved pressed again) and the Document menu's
  Review and Accept, the move to a selection by hand with no translation of
  it, a second move by an area drag clearing the quote and putting no
  picture in the chat, and an account that applies only exact matches
  getting a renumbered citation back lost with its closest match, which
  its card shows, outlines and places, the card the same after it; then a
  hollow highlight's card scrolled out of sight and back, Discard
  deleting a bare one or keeping one with a note off the PDF, the card the
  same after it and Discarded pressed again bringing it back, the
  discarded one kept in the walk, each Discard going on to the next one;
  then a review card's note and counts, the notes panel following Next and
  the advance to its block with the focus kept in the card, the same row
  on a dashed card and a hollow one, Accept after Move… going on to the
  next one to check, and Discard on dashed ones deleting a bare one or
  keeping one with a note, undone in place; then a card on a short window
  giving the note's room before its comparison's, and the Document menu
  counting one moved on its card until Accept; then a hollow highlight's card
  following Show to a closest match pages away, one lost from a page
  the new version dropped reached by Next, its card standing in the viewer,
  Enter on Use closest match to that far page and Enter again with the
  focus kept on the button through the card's hiding, and the lost card's
  steps on one line on a 360 px phone in English and Chinese
  ([highlight_relocation.md](highlight_relocation.md)).
- `translation.mjs`: a two-column page whose paragraph runs over the column
  break: the request carries one text with `{br}`, the formulas and the
  citation marker as tokens, the page and the section; the reply fills both
  columns' boxes, and a formula comes back as a crop of the page inside the
  translated line (against a mocked `/api/ai/translate` and
  `/api/ai/translate/brief`). Then the first chunk landing while the brief
  is made, and the job running on after leaving the PDF to 100% in its
  background-tasks row, asking again for the chunk that went without the
  brief. Also a busy account's refusal waited out and a paragraph left in the
  original until Try again; the keep switch, the kept line and Clear in
  Settings; Forget this translation; and a viewer of the workspace offered
  neither Forget nor Clear. What a change does
  to the requests on real papers is the translation bench's
  ([translation.md](translation.md) "The bench").
- `transfers.mjs`: the Import and Export dialogs — format/source cards,
  the review step and its switches, its two columns lined up and finding
  each other's rows, direct export for fixed formats, the
  export job's finished step, and a folder's annotated PDFs exported in the
  background: the dialog closed mid-way, reopened from Background tasks and
  its zip downloaded ([tasks.md](tasks.md)). The job's listing is held at
  "running" with `page.route`, since the server's job is quick.
- `ink.mjs`: handwriting.
  - The tool strip and its presets: the options row, More, the Tool menu,
    reordering, the bracket keys (`]` at the widest says the width again
    without a write) and a held E. Opening the strip arms the stylus pen,
    not a highlighter before a hidden last pen.
  - The strip's menus (More, the Tool menu, the Style picker's) open 4 px
    under their buttons by the left edge (`assertMenuFrom`). A press on
    More while the Tool menu is open swaps the two, and More opened with
    Enter closes on Escape with the focus left on it.
  - Each of them reads as one menu named by its button, the Style menu as
    Pen style (`assertMenuRoles`). The Tool menu's Hidden tools flyout is
    a menu inside it, named by its row.
  - Mouse strokes becoming an ink block with an `.ink` upload, persistence
    across a reload, the eraser, stroke undo/redo, a lasso move + delete
    and the notes card's jump + outline (`/Ink` in the exported PDF is
    `test_ink.py`). The partial eraser, and one swept in three long jumps
    over two groups: every stroke it crossed cut at its edge, one undo
    entry, which redo brings back whole.
  - What a pen's press is (`pen.mjs`, its own group): with nothing armed a
    drag selects text, highlights it under the text highlighter and draws
    the rectangle in rectangle mode, an armed tool takes the pen over all
    of these, paper is written on, and both shells claim nothing.
  - Pen input: coalesced sample timing, pressure and lift endpoints in the
    uploaded file, prediction, palm suppression and palm-first pen
    takeover, cleanup after `pointercancel` / lost capture. Chromium's
    native touch and pen, and synthetic Pencil events for Safari's handler
    order. Stylus latency and OS palm rejection need a real tablet.
  - The file: a pressure stroke stored as version 2 with its baked width,
    and two tabs changing one group at once (the save merges by stroke).
    A change to a stroke the other tab changed meanwhile is dropped, and
    that tab's pill says so.
  - The build fence: a page whose session names another build than the
    socket's hello stops drawing, behind a sticky pill with Reload. The
    strip opens with nothing armed, and a drag saves nothing.
  - Draw and hold: a held line, and a closed circle and rectangle held
    still where they began, snap to their shapes.
  - The pen's feel: a highlighter always straightened, the eraser's
    filter, Back to last tool, the two-finger double tap, Scribble to
    erase and Smoothing.
  - The pen styles: the Brush's tapers, Dashed and Shift+[ ], a Brush's
    held line at constant width, a tilted Pencil's tilt, shading and
    grain, and the hover footprint.
  - A stroke's `ink-live` chunks: its page, id, look, `n` and samples,
    and no `g` or `k`.
  - Transcribe with AI (the oldest attached block making room), the
    Handwriting rows in Settings, and the draft journal bringing back
    strokes a crashed tab never saved.
  - A browser with pens of its own is asked once, on the account's first
    pull, whether to keep them. *Use this browser's* pushes them, and
    another browser shows them after a reload.
  - The question stays up while a page opens under it, the "Ready." of a
    page that kept it waiting long enough to say "Opening…" showing over
    it for its second, and while a link to a page that is gone opens.
  - A later pull and a browser on the defaults never ask. Nor does a later
    load of a browser that only took the account's list or sent its own,
    by a push or with keepalive as its page was hidden.
  - The next account to sign in on a browser takes its own pens without a
    question, and no key the browser keeps names the last account's pens.
- `inkEditing.mjs`: editing handwriting, on native Chromium touch and pen,
  asserting on the persisted stroke files. `--only "ink edit:"`, and
  `--only ink` runs all five ink files.
  - Tap-to-select and the selection menu, which opens and grows in place,
    frame by frame.
  - Colour/width edits keeping pressure and time, duplicate ids, selective
    delete and the Undo/Redo buttons.
  - A finger lasso-move across groups in pen-only mode, swipe/hold
    arbitration, a pen resuming through a selection and menu placement on
    a small screen.
  - View/edit shares, and an edit share's Transcribe in the chat (in the
    Convert menu) saying a shared page has no AI chat.
  - Copy, cut and paste (Ctrl+C / X / V, and Paste here on a right-click),
    a flip, and New note and Merge into note.
  - New note on a note's whole drawing, and Merge into note on a note with
    a caption, are refused on the pill and change nothing.
  - The menu's first row, in order: Color, Width and style, Copy,
    Convert, More, Delete; Duplicate is in the More row.
  - Width and style opens the pen's width row and the style row, which
    restyles a pen to Brush and Dotted; a highlighter takes a line, never
    a brush.
  - The selection's Style picker opens its menu 4 px under it, or over it
    when the viewer has no room under it, a menu of its own outside the
    selection menu's toolbar. Opened with Enter,
    Escape closes that menu alone and the focus stays on the picker.
  - A tap on the picker opens the menu, the tap's click leaves it open,
    and a second tap closes it.
  - The Color row rings the colour every selected stroke shares, none for
    a mix, and a colour off the palette on the custom swatch. A custom
    colour keeps a highlighter's opacity.
  - Ctrl+D and the menu's Duplicate set the copy off by the same 12 css px
    at two zooms. A Duplicate past the bytes one file holds is refused
    with the selection kept.
  - So are a New note and a Merge into note past those bytes, on a page of
    two groups of about 3.3 MB each.
  - Ctrl+V while the next paper's PDF loads pastes nothing, and once a
    page of it is in view it pastes there.
- `inkShapes.mjs`: the strip's modifiers ([handwriting.md](handwriting.md)
  "What the user sees"). The Shapes tool's dragged rectangle stored with
  its shape record, the ruler and a stroke begun by its edge, Stamp line,
  Escape one layer at a time, the calligraphy pen and its nib, a shape's
  handles and a Fill. It also checks the crosshair as the page's cursor
  with Shapes armed, the Nib angle's − and + (no slider), and that a
  keyboard-focused handle shows one ring, its dot's. `--group ink-shapes`.
- `inkZoom.mjs`: the zoom window (a stroke written in the panel saved at
  page coordinates, Previous / Next / New line, the advance along the
  line, the box dragged, and the grip: a drag shown as it moves, the
  scroller following and nothing written, applied once at the release
  and kept across a reload, ↑ / ↓ at once), the selection menu's
  Transcribe in the chat (Convert's menu), with the AI stubbed (part of a
  group goes as `ink_selections`, a whole group as its block's chip), and
  a picture in the panel, drawn and dimmed as on the page.
  `--group ink-zoom`.
- `inkAi.mjs`: the selection menu's Convert menu
  ([handwriting_ai.md](handwriting_ai.md)), with `/api/ai/ink/shapes` and
  `/text` stubbed by NDJSON answers. `--group ink-ai`, `--only "ink ai:"`.
  - Convert, in the first row after Copy, opens its five rows in a menu
    of its own under it, the AI's then the SVG's.
  - A rough box is redrawn as a stored `rect` shape that is selected, and
    Ctrl+Z brings the drawn stroke back.
  - One request runs at a time, the strokes under the translation's
    shimmering wash while it is out, and the task row's Stop, or leaving
    the page, takes the wash away and leaves the strokes as they were,
    though the held answer then comes.
  - Convert to text puts a text box with the stubbed text where the
    strokes were, in their colour, and removes them, the wash
    shimmering, then landing and going; one Ctrl+Z restores both.
  - Download SVG saves an SVG of the selection's paths, and Copy as SVG
    (with clipboard permissions) puts a PNG and the markup on the
    clipboard.
  - In an edit share's view, Redraw as shapes and Convert to text say a
    shared page cannot use the AI and send nothing.
  - In a 740 × 620 window the menu, with no room under a button low in
    the window, opens above it, never over it; a scroll closes it, as
    More from the keyboard does; and a selection of a highlighter
    alone greys Redraw and Convert to text, and a page with no secure
    context Copy as SVG, each title saying why.
- `notebooks.mjs`: sheets of paper ([notebooks.md](notebooks.md)), written
  on with the same ink tools. `--group notebooks`.
  - A new notebook opens in the notebook view, a stroke on a sheet becomes
    a group under it, and a click on a stroke selects it.
  - Writing low on the last page adds the next, and Add page adds one
    more. Z with the strip open says the zoom window works on PDF pages,
    and opens none.
  - The paper menu sets a page's pattern, and a page added after it takes
    its paper.
  - The side bar switches to the notes view and a page's own switch back,
    the choice outliving a reload. The pages and their handwriting survive
    a reload, and the export is a PDF of the pages.
  - Two fingers pinch-zoom and pan the pages and Ctrl+wheel zooms, both
    holding the paper under them. A page of hundreds of strokes draws them
    in the other view after a switch.
  - Pages in a note: "/note" turns an empty block into a page drawn in
    place, the page and its group's card replay the writing, and "Add page
    below" adds one.
- `recording.mjs`: audio recordings ([recording.md](recording.md)), with a
  stand-in MediaRecorder and Audio. `--group recording`.
  - The mic records into a recording block, and a note typed and a stroke
    drawn meanwhile carry their moment.
  - Play shades the note and fades the stroke until their moment, a tap on
    the stroke seeks, and Escape closes the player.
  - The player's mode and speed menus open on the press, 4 px above their
    buttons. Each is a menu of radios named by its button. A press on one
    while the other is open swaps them, a second press closes it, and
    after Enter and Escape the focus stays on the mode button.
  - In the notebook view, Follow scrolls the viewer to the sheet being
    written on.
  - With no viewer shown, Play docks the player over the notes, and Escape
    closes it.
  - With Record hidden from the viewer's strip, its ⋯ menu starts a
    recording, and the button stays on the strip until it stops.
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
- `pictures.mjs`: pictures ([pictures.md](pictures.md)) on a PDF page and
  on a sheet in the notebook view. `--group pictures`.
  - The strip's Picture button places a PNG on the page in view at its
    aspect, centred and selected, and a reload keeps it. A paste places
    one at the page's centre and a drop at the drop point.
  - A dropped SVG goes up as it is, at the size its root element gives, and
    is drawn through its `<img>`. Stretched with Alt it fills its frame,
    and its URL is a sandboxed download.
  - A drag, the arrows, the handles (Alt stretching, the turn's
    right-angle magnet), the flips, a crop (Escape cancels one), Reset,
    Fit to page and a turn after it each undo and redo. Copy image puts the
    picture on the clipboard as it shows. Duplicate acts on the page and
    in the notes. Bring to front and Send to back restack the page by `z`,
    leave the notes' order and undo as "picture order change". A picture
    pasted then goes over the one brought to the front.
  - Then come the caption typed in its row, a lock (no handles, a drag,
    the arrows, Delete and a double click doing nothing, the short menu,
    undo, redo and Unlock), an opacity (the steps, the image faded on the
    page, in its card and on the clipboard, undo and redo, kept through a
    lock), a stylus, a finger's tap, drag, scroll and pinch, Escape and a
    press elsewhere during a handle drag, a text box
    over the handles, an upload that lands after a box was opened, the
    notes' card and marker, a read-only visitor, an edit share's drop, and
    two people on one picture (a move while the other crops and types the
    caption, and a drag let go after the other's lock).
  - A drop on a sheet drawn among the notes leaves no row outlined.
  - Last, the lasso takes a picture with a stroke and leaves a locked one.
    A drag carries both, and one undo and one redo step both, from the
    strip and by Ctrl+Z. With its row's Pictures off it takes the stroke
    alone.
- `guide.mjs`: the first-run guide ([onboarding.md](onboarding.md)),
  started from the account menu's Tours — every registered home-view anchor
  is present once, the demo step adds a paper by itself (pointed at an
  uploaded PDF through `gamma-guide-vars`, so no network), the user's
  highlight checks the next step off, finishing records "done", the menu
  restarts it, and Esc leaves a replay. `contextualGuide.mjs` (the AI chat
  tour, and each step's card opening beside its own anchor) and `triggeredGuide.mjs` (offers and hints): onboarding.md "Files".
- `ipad.mjs`: the installed web app ([ipad.md](ipad.md)): the manifest
  and its icons, `theme-color` following the theme, the standalone-mode
  block in the bundled stylesheet (`display-mode` cannot be emulated in
  Chromium), taps in the library, the upright and landscape layouts, the
  phone's bottom bar, and a note editor's editing bar by tap (none with a
  mouse). Its `ipad shell:` steps run the page under a stand-in host
  (`fakeShell`). A Pencil stroke is claimed in the preset's look and
  committed from the host's samples (with tilt, and a calligraphy nib
  turned by the roll). The host's `hold` snaps and refits a still line
  or opens Paste here. Then come a claim along the ruler, a shell
  without `hold`, the View menu's *Server bar* row (landscape, under More
  upright, and none without `shellBar`), and the Apple Pencil gestures,
  settings and haptics. The files the system opened in the app go where
  picked ones would, an image as a picture on the page in view.
  `--group ipad`.
- `android.mjs`: the Android app's shell ([android.md](android.md)), with
  the app's own document-start script
  (`android/app/src/main/assets/shell/host.js`, its HostInfo filled in)
  run as the app injects it, over a stand-in for its message port that
  carries JSON strings both ways. The host is found and spoken to through
  the script's `post`; the theme and the layout by orientation; a
  stylus's claim and stroke, a declined claim left to the page, the hold
  and its tick, the pen's two buttons; the Stylus settings and their
  search, gated by the gestures the host forwards, and no *Lock angle*
  without a barrel roll; the saves the app takes (a blob in chunks, a URL
  by itself), a refused one, and no browser download; a file the app was
  given; the shell's bar; no folder picker; the server inside the app on
  Settings › Server; and the chrome pages (`desktop/ui`) under the app's
  `chrome.js`, beside the iPad's and the desktop's. `--group android`.
  Both shell groups take their stand-ins from `tests/e2e/shells.mjs`:
  `fakeShell` answers every message as the shell would (claims, files,
  the bar, saves, recording what the page posts), the iPad's through
  WebKit's handler and Android's through the port, and `androidShell`
  adds the app's script and its HostInfo.
- `touch.mjs`: one behaviour for mouse and finger
  ([ui-design.md](ui-design.md#one-behaviour-for-mouse-and-finger)), in
  desktop and touch contexts — a window's fold, unfold and drag, a held
  finger and the "⋯" for every menu, Select mode, the folder menu's moves,
  the View menu's command rows, the pill's Undo, a notebook's place across
  its views and tabs, the viewer's touch tools, the editing bar's marks,
  and the chat's composer and history rows. Its last step reads those
  menus as assistive technology does
  ([ui-design.md](ui-design.md#menus-and-submenus)). Each is one
  `role="menu"` around its items, with no role between them but a group.
  A flyout sits inside its parent, and a menu a control opens is named by
  it. `--group touch`.
- `pdfTouch.mjs`: 400% rendering under an emulated canvas limit, distant-page
  release/repaint, live ink, native touch swipes ([pdf_loading.md](pdf_loading.md)).
  `--only "pdf touch"`.
- `pdfload.mjs`: PDF loading, in a non-default workspace — the timing probe
  (a 300-page, 20 MB document opened cold at an emulated 20 Mbps, the
  IndexedDB backfill, a warm reopen, a same-tab return; reports the per-phase
  `performance.mark("pdf-<phase>")` stamps and the bytes on the wire as each
  step's note, asserts only that it paints), then the behaviours: page boxes
  from the manifest (a landscape page below the fold), fit-width sized to the
  text pages past a small cover (with the manifest and measured), the last-read page
  after a reload, two large documents through the parsed-document cache, a
  paper's tab coming back drawn in every frame where it was left (from a
  notes page, in a browser launched with its scrollbars shown, and between
  two papers read at different zooms), the anonymous share view by ranges
  ([pdf_loading.md](pdf_loading.md)).
  `npm run e2e -- --only "pdf load"`.
- `files.mjs`: files dropped on a block row / the page body become file
  chips (a `dropFiles` helper builds a real DataTransfer; the paste step
  builds a `ClipboardEvent` in the page, since Playwright's `dispatchEvent`
  cannot), a PDF chip's right-click "Add to library" makes the document page
  in the project's folder and the chip gets an open-page button, a markdown
  chip's "Add to library" imports a note page and leaves the file untouched,
  the upload endpoint's lab-file / executable rule, a slow upload
  stopped from Background tasks, and the Document popover's "Replace the
  PDF…" swapping a document page's file while its title and highlights
  stay and "the highlight follows its text" (the run's summary in the
  status pill, the highlight's new place and quote, matched roughly and
  drawn dashed, a second tab following).
- `collab.mjs`: two accounts in a shared workspace: presence, live ops, edits
  to different blocks, same-block last-writer-wins, undo after a remote edit,
  rename propagation, edits made offline replaying, remote delete, a
  highlight made by the other person. The stroke one person is drawing
  shows on the other's page within 700 ms (`ink-live`), and an undo right
  after the lift takes it away at once.
- `auth.mjs`: refusing an inaccessible explicit workspace without opening a
  different library, the login page, guest login (a new throwaway account
  each time) and demo mode ([guests.md](guests.md)).
- `motion.mjs` ([ui-design.md](ui-design.md) "Motion"): a menu grows from
  the cursor and leaves through an inert copy the next menu takes the
  place of; a closed dialog fading out is not open; the outline's, the
  library's, the tab strip's, the Inbox's, Background tasks', the Comments
  window's and the PDF margin's lists glide, come in and leave as the doc
  says, and loads, another client's edits and Load more move nothing; a
  segmented control's frame slides; the share popover and Recently deleted
  open on what they had; a theme change draws every colour in one frame;
  and under reduced motion each of them moves nothing and leaves no copy.
  `npm run e2e -- --only motion`.
- `i18n.mjs`: Settings → Appearance → Language switches the interface to
  Chinese and back through the reload ([i18n.md](i18n.md)). `--only i18n`.
- `share.mjs`: the share dialog, the anonymous share view (PDF, highlight,
  image through the share token, no editor), an edit share, a folder share
  copied into another library by the listing's Add to my library and, on a
  second server, by its link pasted into +; a page inside a shared page
  naming the link that opens it, a move into a shared page asking first,
  and an edit link's visitor making a page inside the shared page.
- `subPages.mjs` (group `sub-pages`; [home_library.md](home_library.md#sub-pages)):
  /page in a filed page makes a page inside it, kept out of the library and the folder's count, listed in the parent's "N pages
  inside" panel, its crumbs leading back; Quick open shows its path; "New
  page inside" opens the new page with its title in edit; the tab menu's
  "Move to library" files it in its top's folder and the page menu's "Move
  into page…" puts a library page inside; deleting the parent names the
  pages inside, Recently deleted lists one entry "with N pages inside" and
  Restore brings the tree back, as does the missing-page notice of a link
  to a page inside (which then opens that page, not its parent) and the
  open page's own notice when its parent was deleted elsewhere; a page
  share of the parent opens the pages inside from the panel, the chip and
  the crumbs, a share of a page inside shows nothing above it, and a
  folder share reaches them under their top; a label on a page inside
  counts in its tile and lists in its view, in the library and in its
  top's folder; the search panel's folder chip keeps a hit inside a page
  inside; a page inside's Page properties show "Inside page" under Filing,
  whose Open page opens the parent; a Notion "Markdown & CSV" zip imported
  through the Notion pages card into a folder is reviewed with each page under its parent and comes in
  as a page tree, a database's rows inside its page, only its top pages
  filed and no folder made for its directories; a page's export offers
  "Include pages inside" off by default (Gamma gets a review step for it),
  and on, the job's zip nests the pages inside with `parent:` front
  matter, while a page with none shows no switch. The steps check the
  stored state as well as the screen. `--group sub-pages`.
- `settings.mjs`, `folders.mjs`, `trash.mjs`, `quickOpen.mjs`, `mcp.mjs`
  and `cloudSignIn.mjs` are groups of their own. `quickOpen.mjs` holds the
  tabs' steps too: a tab pressed selected in the press's own frame while
  the page it leaves stays whole, and "Opening…" only for an open that
  keeps the reader waiting. The headers of
  `folders.mjs`, `trash.mjs` and `cloudSignIn.mjs` name the doc each
  covers. In `cloudSignIn.mjs` a workspace's invite dialog offers E-mail
  only once the server signs in through a client of its own. Two of its
  steps start a Gamma under the `invited` policy, with a client of its
  own, against `fakeCloud.mjs`, which keeps the invitations it is asked to
  mail and to cancel. What the mail's link ends in,
  `start?next=&invite=<link id>`, is one helper (`accept`). One step
  shares a page by e-mail: the row's role changed and a double-clicked
  resend mailing once; a newcomer whose sign-in from the link's gate,
  under the invited address, only asks to join (her address claims
  nothing) until the mail's link lands her on the page with the
  invitation's role; a request to join approved in the Share popover; a
  namesake of the owner who asks to join and lands as `lab-owner-2`; and
  a request from the login page approved under Server → Sign-in
  (`--only "cloud sign-in: a page shared"`). The other invites by e-mail
  from Settings → Users (one sent, one withdrawn), from a shared
  workspace's Manage page (its role changed, mailed again, withdrawn) and
  from the Share popover. The newcomers follow the mail's link under
  another address than the invited one and land with access; a resend of
  the used invitation finds it gone. The address of someone with access
  already is mailed like anyone's ("Invitation sent to …", a pending
  row), and its link keeps the role she has (`--only "cloud sign-in: an
  invitation by e-mail"`).

Every step also asserts that no API call failed (4xx/5xx), no console error
and no page error happened meanwhile (`openPage` records them;
`EXPECTED_FAILURES` in the harness lists designed refusals such as the
metadata fetch's 404 for a PDF without identifiers). Wait for the state a
step needs with `until()` / `waitForFunction` / `waitForEvent`, never a fixed
`sleep` — the one left (the view-only share's "no editor opens") is a
negative check with nothing to wait for. New UI work touching the
save path, workspaces, auth or rendering of URLs should add a step here; the
`/verify` skill runs this suite.

### The Android app's tests

From `android/`, with JDK 21, the Android SDK and a Python 3.13 (the
toolchain: [android.md](android.md) "Building"; the commands:
[android/README.md](../../android/README.md)); `bash ./gradlew` from Git
Bash, `gradlew.bat` from PowerShell. What each test covers:
[android.md](android.md) "Tests".

```bash
./gradlew --no-daemon :app:testDebugUnitTest     # the JVM tests, no device
./gradlew --no-daemon :app:assembleDebug         # a debug APK, app/build/outputs/apk/debug/
./gradlew --no-daemon :app:connectedDebugAndroidTest -Pgamma.abis=x86_64   # the device tests, on a running x86_64 emulator
```

- The JVM tests (`app/src/test/`) cover the Kotlin with no `android.*`
  import: the pen's core, the bridge and its port's wire, the shell's
  model and state, the cloud sign-in, the opened files and saves, and the
  local server's rules with its runtime faked. The pen's tests read
  `tests/shared/inkwidth.json` and `inkhold.json`, the rows the web's and
  the iPad's tests read (the build passes the repository's path as
  `gamma.repo`).
- The device tests (`app/src/androidTest/`): `LocalServerStartTest`
  starts the server inside the app for real (health, a sign-in, a PDF
  upload, the page, `pdf-info` and a page picture from PyMuPDF), and
  skips when the app had already started its own server in that process;
  `PenLayerTest` feeds the pen layer real MotionEvents (a stylus nobody
  claims, the button held at the down, a claim, a button clicked while
  the pen hovers).
- The first build runs Chaquopy's pip, which needs the network and a
  Python 3.13 (`-Pgamma.buildPython=<path or command>` when Chaquopy's own search
  misses it), and then caches it. A build also builds the web app when
  `frontend/dist` is stale, which needs Node; `-Pgamma.webBuild=prebuilt`
  takes `frontend/dist` as it is. A debug build holds both ABIs unless
  `-Pgamma.abis` names one.
- CI: `android.yml` runs the JVM tests, an unsigned release build and the
  device tests on an emulator for pull requests that touch the Android
  app, the server's mobile entry or its pins, the two ink fixtures or the
  chrome pages ([github_actions.md](github_actions.md#androidyml)).

## Debugging surfaces

- **Server log** — Settings → Server → "Server log" (admin only): the
  in-memory ring buffer behind `GET /api/admin/logs`, filterable to
  warnings / errors; the Dashboard above it counts them since startup and
  shows the build and the update check. Backend code must log through
  `gamma/logbuf.py`'s `log` (never `print()`); use `log.warning` for what an
  admin should notice. Secrets are masked at insert time. Gone on restart.
- **"Invalid HTTP request received from <peer>: …"** is uvicorn's warning for
  bytes that are not HTTP/1.1, which `logbuf._InvalidRequestDetail` extends
  with the TCP peer and what arrived: a TLS handshake (https:// sent to the
  plain-HTTP port), HTTP/2 without TLS, a PROXY protocol header, or the first
  line, with its query cut off, and the parser's complaint. A stream of TLS
  handshakes means the port is reachable from the internet, where scanners
  try https:// on every port. The usual cause is a raw TCP tunnel (FRP
  `type = "tcp"`) whose remote port is open publicly beside the reverse
  proxy in front of it. The peer is then the tunnel's local end, an address
  `FORWARDED_ALLOW_IPS` lists, so anyone on that port can choose the client
  address rate limits count by. Close the remote port to everything but the
  reverse proxy.
- **"[collab] a page socket reached the server as plain HTTP …"** is a
  warning, logged once an hour at most: a proxy in front dropped a page
  socket's `Upgrade`, so the request reached the server as a plain `GET
  /api/ws/page/{id}`, answered 426. Each attempt also leaves an `[http] GET
  /api/ws/page/… status=426` line. The tabs behind it poll the page's op
  log instead ([collab.md](collab.md) "The client", "without a socket"):
  others' edits arrive within about 2 s, and nobody sees anyone's cursor.
  In the browser's Network tab such a tab shows `ops?since=` every 2 s
  and a socket attempt about once a minute. The fix is the proxy's
  WebSocket settings (`README.md`, the reverse-proxy paragraph). A
  TLS-inspecting proxy that holds the socket without passing anything
  polls the same way, but logs nothing here.
- **"WebSocket from <peer> dropped: [WinError 121] …"** is an info line for
  a page socket whose peer stopped answering: a laptop asleep, Wi-Fi gone.
  The websockets library logs it as the error "data transfer failed" with a
  traceback, because Windows reports it as a plain `OSError` where the
  library looks for a `ConnectionError` or `TimeoutError`;
  `logbuf._DroppedWebSocket` makes it one line, so it raises no
  `log-errors` notice. The connection is closed either way and the page
  reconnects. A "data transfer failed" error that remains is some other
  exception and worth a look.
- **`[ai_translate]` warnings** trace the PDF translator
  ([translation.md](translation.md) "The server"):
  - "N of M segments came back missing, doubled or with a broken token —
    asking for them again" is a reply that left out, doubled or emptied a
    numbered segment, or broke a `{v1}` token. They go again in one call.
    A segment still wrong after it shows the original, and the viewer
    offers to try again. Many such lines mean the model keeps losing
    segments: a better instruction follower serves translation better.
  - "asking again failed: …" is that one call failing as a call.
  - A bare "[ai_translate] <error>" is a chunk's own call (or a
    translation service) failing after its retries: a 502 for a plain
    request, an `{error}` line in a stream. The viewer asks once more,
    then leaves the chunk's paragraphs in the original and goes on.
  - "the paper's brief: …" is a brief request that failed outside its
    model call.
  - `[translate]` lines are the brief itself: "making the paper's brief
    failed: …" and "the paper's brief came back unusable" are remembered
    for 30 s, during which the chunks translate without a brief; "the
    paper's brief was not asked for: …" is Gamma's own refusal (the calls
    all in use, the allowance spent), not remembered.
  - `[translate_store]` lines are the kept translations' own sqlite errors,
    which never fail a translation, and "… has 20000 kept paragraphs …;
    not keeping more" (`MAX_ROWS`, once per document per process).
- **`[ai]` retry lines** ("<model>: <why> — retrying in 1s (attempt 1 of
  2)") are info lines: a provider call that failed before its reply began
  and is tried again ([ai.md](ai.md) "Calls open at once"). Many with a
  connect timeout (`WinError 10060`) mean the provider is hard to reach
  from this server.
- **`[relocate]` lines** come from the highlight run of a replaced PDF
  ([highlight_relocation.md](highlight_relocation.md)). The warning "the
  job did not start (…)" is a replacement whose run was refused (the
  account's background tasks full, or the last run for the file still
  stopping): its answer carried `relocation_error`, and Find highlights in
  this PDF places the highlights. The info line "the file … is no longer
  stored" names an old file gone from the uploads, whose highlights were
  placed by their quotes. A run that failed is a `[jobs]
  relocate-highlights … failed` line: "changed again" (the page carries
  another file now) and "was deleted" are expected, the rest is the new
  file (not stored, unreadable, no text layer).
- **Two request lines, on purpose.** The console carries uvicorn's access
  log for every request (`INFO: 127.0.0.1:… "GET /api/… HTTP/1.1" 200 OK`)
  except the answered polls (`logbuf._QuietPolls`): the Background tasks
  tray's `GET /api/jobs`, which comes every 1.5 s while a job runs, and a
  page's `GET /api/pages/{id}/ops?since=`, which a tab without its socket
  sends every 2 s. Likewise a `[jobs]`
  line marks how a job ended, except a search-indexer run that simply
  finished. Interleaved with the access log comes Gamma's own line for the ones worth reading
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
  (downloads, AI calls, PDF parsing), is a **sync `def`**, so FastAPI's
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
  the API incompatibly. Do not let a dependency refresh move it. A folded
  window leans on how v2 works (`renderSlotGroup` in App.jsx): a panel
  registered again, its `order` changed, makes the group read its saved
  layout, and a splitter moves the panels by its index among the
  splitters, past any pinned at no size.
- React stays on 19.2 (`~19.2.9` in `frontend/package.json`), so a
  dependency refresh takes its patches but not 19.3. 19.3 adds about 7 KB
  of Brotli to the entry chunk, which every first screen loads, for view
  transitions and Fragment refs, which Gamma does not use. Moving to it is
  a decision, not a refresh ([research/react-19.md](../research/react-19.md)).
- `inert` is a boolean prop: `inert={!!folded}`. React 19 reads the empty
  string as false, so `inert=""` makes nothing inert, and it says so only
  in development. `tests/inert.test.mjs` fails on a string given to it.
  `npm run dev` runs StrictMode, which in React 19 attaches each callback
  ref twice as an element mounts (the element, null, the element again).
  A ref that does work on attach or detach must allow for that, as
  `useTextScale`, `useWheelPan`, `useLeave` and the comment margin's card
  refs do. The browser suite drives the production build, which never
  does this.
- Timestamps are UTC ISO strings with `Z` (`page_now()`); keep the format.
  `db.format_stamp` writes a datetime in it and `db.parse_stamp` reads one
  back (None when unreadable — whether that means expired, due or now is
  the caller's decision).
