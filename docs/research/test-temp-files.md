# Test runs' temporary files and stalled runs

Surveyed 6 November 2026 (dev at `3bbf06fb`) on the Windows development
machine: what the suites left in the system temp directory, why each kind
stayed, why a pytest run sat for five days, and what was done about both. The
mechanics are in [dev/debugging.md](../dev/debugging.md#what-a-run-leaves-behind).

## What was there

| Folders | Count | Files | GB | Made by | Why it stayed |
|---|---|---|---|---|---|
| `gamma-test-*` | 24,881 | 1,203,906 | 85.16 | `backend/tests/conftest.py` | Nothing removed it. One per pytest process. |
| `gamma-e2e-*` | 498 | 85,419 | 5.88 | `frontend/tests/e2e/harness.mjs` | Kept whole after a failed step; a killed run never reached `stop()`. |
| `gammacloud-test-*` | 542 | 226 | 0.06 | `cloud/tests/conftest.py` | Nothing removed it. |
| `gamma-import-reviews-*` | 3,283 | 748 | 0 | `gamma/import_staging.py` | Outside the data dir (below). |
| `i18n-*` | 1,667 | 317 | 0 | `frontend/tests/i18n.test.mjs` | One test, no removal. |
| `gamma-probe*`, `gamma-collab-probe*` | 342 | 0 | 0 | No code in the repository | One-off scripts; the newest was from 27 October. |
| `gamma-media-profile-*` | 43 | 0 | 0 | `tools/readme-media/runtime.mjs` | The browser profile was not removed. |
| `gamma-bench-tex-*` | 28 | 5 | 0 | `frontend/tests/bench/translateBench.mjs` | No removal. |

That is 31,284 folders, about 1.29 million files and 91 GB, 94% of it
`gamma-test-*`. Sizes were read while other runs were writing, so the last
digits move.

A run cost more on a machine with many cores. Every xdist worker imports
`conftest.py`, and so does the controller, so `-n auto` on 24 CPUs made 25 data
directories and left about 0.5 GB. The last 48 hours held 3,230 folders and
36.5 GB.

Many old folders kept their structure with no files in them. Storage Sense is
on here with its temporary-file cleanup enabled, which fits, though nothing
shows it did that. It would not have been a fix either way: the folders stayed.

Of the 498 `gamma-e2e-*` folders, 355 had a `failures/` folder (160 with files,
195 emptied), 70 were whole with no failures (killed runs, and the ones running
during the survey), 73 were empty, and none was partly removed. So a
`fs.rmSync` failing on Windows was not a cause. Their 5.88 GB was 3.75 GB of
`dist/` copies, 1.64 GB of `data/` and 0.47 GB of `failures/` and logs. The copy
was the largest part and told a reader nothing: `failures/*.log` already holds
the server log's tail.

## Why the files stayed

1. **Nothing owned the folder.** `mkdtemp` at the top of a `conftest.py` had no
   matching removal.
2. **A file left its owner's folder.** `import_staging._root()` is
   `gamma-import-reviews-<hash of the data dir>` under `tempfile.gettempdir()`.
   Removing the data dir misses it, and its own sweep runs at that instance's
   next upload, which a finished test instance never gets. The e2e harness
   removed its folder and still leaked this one.
3. **Cleaning up took a clean exit.** `run.mjs` handled no signals, and a
   pytest-timeout hang ends the worker on the spot. Neither ran any removal.
4. **Too much was kept.** `Server.stop()` kept the whole folder after any failed
   step.

## The run that stalled

A `pytest tests -n auto --dist loadfile` started on 2 November was still up on
the 6th, 4.9 days later, with 24 s of CPU on the controller and about a hundred
processes (24 workers and the controller, each a venv launcher and an
interpreter). The agent session that started it was gone. `py-spy dump` read
the live stacks:

- The controller was in `DSession.loop_once`, waiting on its event queue.
- 23 workers had finished and sat in execnet's
  `WorkerPool.integrate_as_primary_thread`.
- One worker was in xdist's `TestQueue.get`, waiting to be sent tests.

So the controller waited for a worker that waited for the controller, outside
any test. What lost the message was not found. It does not need to be: the
timeout in `pytest.ini` runs per test and pytest-timeout's session timeout is
only checked between tests, so nothing in the run could have ended this. The
same machine had a server on port 9100 up for eight days, also from a session
long gone, started by hand as the verify skill describes.

## What was built

Point the run at one directory it owns and let everything follow, rather than
add a removal at each of the places that write, and have every process able to
end itself.

- **pytest:** `tests/testrun.py`, imported by both conftests.
- **The browser suite:** the backend's temp variables point into its folder, a
  kept run drops `dist/`, `run.mjs` sweeps, a process with no step finished ends
  itself, and workers and backends leave with their parent.
- **One-off servers:** the verify skill starts them under `timeout 2h`.

As measured on Windows at the time:

| Check | Result |
|---|---|
| Backend suite, `-n auto`, real `%TEMP%` | 7,592 passed, 58 skipped; no folder left (7,791 files before) |
| Account server suite | 401 passed; no folder left |
| A test sleeping 900 s under `-n 2`, limit set to 8 s | Exit 1 at 10 s, workers gone within 18 s more, `loop_once` in `stalled.log` |
| Browser suite, its own temp directory | 701 of 701 steps; the directory empty after, Playwright's folders included |
| A step that never ends, limit set to 6 s, `--jobs 1` and `--jobs 2` | Exit 1 in 9 s, no node or backend process left |
| A two-worker browser run with only its parent killed | No worker, backend or browser left |

Re-arming the watch after every report cost 3.7 s across a run's 22,000 reports
(a new thread each time), so it re-arms at most every 30 s, which a 15-minute
limit does not notice.

## What was turned down

- **pytest's own sessions** (the data dir under
  `config._tmp_path_factory.getbasetemp()` in `pytest_configure`, with
  `tmp_path_retention_policy = failed`). It worked on a 62-test subset, with the
  session locks handling killed runs and a failed session kept. But it moves
  `GAMMA_DATA_DIR` from import time to a hook, and an `import gamma` in between
  would land on the developer's own `data/`. It also leans on a private
  attribute.
- **A fixed limit on the run.** Simpler, but it has to sit far past a serial run
  on a slow machine, so a stall would hold everything for hours. Progress ends
  one in 15 minutes and does not bound a long healthy run. The same rule cannot
  go on a worker, which is idle while the others finish: it would turn a healthy
  worker into a crash. Workers get the fixed limit instead.
- **`faulthandler` writing to stderr.** A full pipe would block the dump, and
  with it the exit that follows. Hence a file. The cost is that a stalled
  controller says nothing on its way out.
- **A live-process check for the sweep.** A run cannot outlive its limits now, so
  an age is enough and works the same in Python and Node. `os.kill(pid, 0)` is no
  test on Windows, where signal 0 is `CTRL_C_EVENT`.
- **Moving staging into the data dir.** A product change for a test problem, and
  the redirect covers the export spools too.
- **A reaper that kills old test processes.** Each process ending itself needs no
  rule for telling one session's run from another's.

## A Windows path limit to remember

`CreateDirectory` refuses a path of 248 characters or more, and staging's paths
are long. `test_import_review.py` passed 19 of 19 with a 247-character lock path
and failed 17 of 19 at 248. `claim()` turns the `FileNotFoundError` from
`lock.mkdir()` into a 404, "import review not found", which names the wrong
thing. A temp directory of about 116 characters failed 50 tests of the unchanged
suite, `test_migrations` and `test_backup_safety` among them. A run driven with
`TEMP` under a deep folder therefore reports failures that are not real. At
`%TEMP%`'s usual length nothing comes near the limit, even with this design's two
extra levels.
