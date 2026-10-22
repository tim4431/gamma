# Where the test suites spend their time

The question: the `check` workflow had grown slow, and recent commits had
added workarounds to keep the browser suite green. Where does the time go,
which tests are actually flaky, and which workarounds hide a cause that
can be fixed? Measured in October 2026, on the CI runs of 21–22 October and
on the developer's Windows machine. The mechanics now in force are in
[dev/debugging.md](../dev/debugging.md#browser-end-to-end-suite) and
[dev/github_actions.md](../dev/github_actions.md).

## The check workflow's jobs

| Job | Time | Of which |
|---|---|---|
| backend | about 3.5 min | 2,200 tests in about 65 s with `-n auto` (locally); the rest is installs |
| frontend | about 1 min | 800 unit tests in 13 s; the Vite build in about 21 s |
| e2e | 7 to 14 min | the suite itself about 6 min (375 s); `npm ci` 48 s in one run and 7 min in another (the registry, not the tests) |

The browser suite sets the workflow's length. Its 32 groups summed to
about 1,080 s on the runner's 3 workers. Per group: settings 148 s,
notes-pdf-share 121 s, triggered-guide 78 s, chat-navigation 67 s, touch
63 s, guide 61 s, contextual-guide 60 s, everything else under 45 s. The
slowest single step is the first-run tour (52 s): it plays the tour's
demos (the typing, the drag, the rectangle) in real time, and checks the
animation itself, so the guide's reduced-motion path cannot stand in for it.

Two smaller costs:

- `manage.py` takes about 1.1 to 1.6 s a call (Python start and the
  `gamma` imports), and the scenarios call it about 60 times to make
  accounts, admins, shared workspaces and members. The inbox and comments
  groups each spend about 6 s that way before their first step.
- Fixed sleeps add up to about 48 s across the suite. Most are negative
  checks ("no offer within 1.5 s"), which have nothing to wait for.

## Flakiness

Of the 17 failed `check` runs between 30 September and 22 October, most
were real failures: a missing `PIL`, a circular import, an argparse
change, backend tests that broke with the PyMuPDF switch, a touch step
still expecting a menu without Export. The browser suite's own flakes
were a transfers step that read the export dialog before the job's
result arrived, the folders label step, two triggered-guide steps, and
the inbox group's setup ("fetch failed"), which had aborted uncounted in
every CI run until the summary started counting an aborted group. None
of them failed again in the later runs, no step failed repeatedly, and
none was a candidate for deletion.

## The workarounds and their causes

- **The fetch retry.** The harness wrapped the global `fetch` to send a
  request again on a closed socket (`UND_ERR_SOCKET`, `ECONNRESET`,
  `EPIPE`, up to three times), for every request in the suite. The cause
  was `server.manage()`, which ran manage.py with `execFileSync`: a few
  calls in a row blocked Node past uvicorn's 5 s keep-alive, so Node had
  not seen the backend close the pooled socket and sent the next login
  down it. Replaced by an asynchronous `manage()`, which keeps the event
  loop running; the retry is gone.
- **The PDF drag helper's re-drag.** `dragSelectPdfText` measured the
  needle's text for two still frames, dragged, and dragged again up to
  three times when the selection was something else, then returned anyway.
  The wait for still text is the documented pattern (two equal readings);
  the re-drag hid a failure behind a pass. It now waits for two equal
  readings 100 ms apart, drags once, and throws with what was selected.
- **The aborted-group count and the HEAD in the PDF meter** were real
  fixes, not workarounds: an aborted group now fails the run, and the
  pdf-load meter no longer counts the viewer's size HEAD as a whole file.

## What was done for speed, and what was not

Done: CI runs the suite in two shards on two runners (`--shard k/n`). Each
group's CI seconds are kept in `GROUPS` as `s`, and `shardOf` gives each
group, longest first, to the shard with the least time so far. The two
shards come to 540 and 539 s of group time, about 3 min each on 3 workers,
against 6 min for the whole suite on one runner. The duplicated install and
build add about a minute to each runner, but the runners run at the same
time.

Declined:

- **Deleting steps.** The suite's time is spread over about 400 steps,
  each guarding a described behaviour, and the failure history named none
  as a repeat flake.
- **Users and workspaces through the admin API.** It would save most of
  the `manage.py` time, but a member added over `PUT
  /api/workspaces/{ws}/members/{username}` goes through the invite path
  (`by` the owner, with pending members), which is not what
  `manage.py set-member` does, so the scenarios would set up something
  different.
- **Concurrent `manage.py` calls.** Several writers on `users.db` at once
  would trade seconds for a chance of a locked database.
- **Reduced motion for the first-run tour.** It would cut its 52 s but
  stop checking the demos' animation, which the step exists for.

## Still open

- `settings` (148 s) bounds a shard. Splitting it in two groups is the
  next step if the suite grows.
- `manage.py` imports FastAPI and the backup and job modules for every
  command; lazy imports there would make each call cheaper for the suite
  and for operators alike.
- The `s` values drift as scenarios change; nothing checks them against a
  run.
