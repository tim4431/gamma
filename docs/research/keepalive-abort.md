# Aborting a keepalive request to make room for another

The question: the page's keepalive ledger gives the queued page edits
first claim on the 64 KiB of keepalive bodies a page may have in flight,
but a handwriting upload sent with keepalive when the tab was last hidden
can still be on its way when the tab closes, with the edits grown past
the room it leaves them. Can the ledger abort that upload so the edits'
POST goes with keepalive in its place? Measured in October 2026 in
Playwright 1.63's headless Chromium 153 and WebKit 26.6 on Windows; what
the ledger does instead is in [dev/collab.md](../dev/collab.md#keepalive).
A second round on 21 October 2026 measured what each close the
automation offers lets through, and the counting rules the ledger rests
on ("Every close the automation offers"). A third round on 22 October
2026 ran the same cases in Playwright's Firefox 155. Its results stand
beside the others, and what only Firefox does is in "Firefox stops the
first `pagehide` listener". A fourth round, the same day, closed the app
itself right after a change, in all three engines ("The app at a
close"). A fifth closed it again once every sender sent from both close
events ("With every sender on both events").

## An abort frees nothing in time

A Node server on a free port held a request: it read the body and did
not answer until told to. In a fresh browser context for each run, the
page sent a 50 KiB keepalive POST (A), which the server held, waited
300 ms for its body to arrive, then sent a 30 KiB keepalive POST (B).
Together they come to 80 KiB, so B is refused while A counts. Each run
aborts A through the `AbortController` its fetch was given, then sends B
at a different moment:

| B sent | Chromium | WebKit | Firefox |
|---|---|---|---|
| A not aborted | refused | refused | refused |
| right after `abort()` | refused | refused | refused |
| after a microtask | refused | refused | refused |
| after A's `AbortError` was handled | refused | refused | refused |
| in the event listener that aborted A | refused | refused | refused |
| a task later (`setTimeout` 0) | sent | refused | refused |
| 50 ms or 500 ms later | sent | refused | sent |
| once the server answered A | sent | sent | sent |
| A's headers in and its body held, aborted, B at once | refused | refused | refused |

Firefox ran each row 5 times, with the same answer every time.

A refused B is a network error (`TypeError: Failed to fetch`, `Load
failed`, `NetworkError when attempting to fetch resource.`) and never
reaches the server. Chromium and WebKit did not stop an aborted
keepalive request on the wire: the server's socket for A stayed open for
the 3 s watched, while an aborted plain request's socket closed (at once
in Chromium, within a second in WebKit). In those two an abort only
rejects the caller's promise with an `AbortError`, and the request
carries on and may land. Firefox closed A's socket at the abort, as it
did a plain request's (45 of 45 aborted keepalive requests). Chromium
stops counting the request's bytes one task later, Firefox after a task
but within 50 ms, and WebKit only once its answer is in. So a keepalive
sent in the task that aborted A is refused, and a ledger that freed A's
bytes to send the edits with keepalive would lose them where a plain
fetch might have landed.

## What a real pagehide lets through

The same held A, then the page went away. Its `pagehide` listener sent
a 1-byte keepalive marker, aborted A or not, sent B with keepalive (and a
plain copy C once B was refused), and sent a plain POST D of its own:

| The page | Chromium | WebKit | Firefox |
|---|---|---|---|
| navigates away, A aborted or not | B sent, D sent | B sent, D lost | B refused, C and D sent |
| reloads | B sent, D sent in one run of two | B sent, D lost | B refused, C and D sent |
| closes (`page.close`), A aborted or not | B refused, C and D sent | B refused, C and D lost | B, C and D lost |

A page that leaves for another (a navigation, a reload) stops counting
its earlier keepalive requests in Chromium and WebKit: B went with
keepalive beside the held A, where a ledger counting A would have sent it
plain. That plain copy was lost in WebKit and in one Chromium reload.
Firefox kept counting A and refused B in all 41 of its navigations and
reloads, and C and D arrived in each. A closing tab counts A whatever the
page does, and refuses B. Closing the page, Firefox let only the marker
through (20 of 20 runs).

## Every close the automation offers

Measured on Windows 11, headless only, with Playwright 1.63.0, on 21
October 2026 (Firefox on 22 October):

| Build | Version | Playwright's build |
|---|---|---|
| Chromium headless shell, Playwright's default headless | 153.0.8010.12 | `chromium_headless_shell-1243` |
| Chromium in its new headless mode (`channel: "chromium"`) | 153.0.8010.12 | `chromium-1243` |
| WebKit | 26.6 | `webkit-2359` |
| Firefox | 155.0 | `firefox-1543` |

The server was Python's `ThreadingHTTPServer` on 127.0.0.1. For each
request it logged when it came, its body's bytes, whether the body came
whole, when the client hung up and whether the answer could be written.
It answered at once, or read the body at once and held the answer 2 s,
or left the body unread for 2 s on a port with a 4 KiB receive buffer.
In `pagehide` and in `visibilitychange` (hidden), the page sent a
keepalive fetch, a plain fetch and a `sendBeacon`, in that order. Each
carried a body of 1, 60, 63 or 65 KiB, or 1 MiB. The page logged each
event to a second origin with bodiless keepalive POSTs, which take none
of the 64 KiB. Each case ran 5 times: the Chromium builds and WebKit
side by side, Firefox a day later.

The closes:

- `page.close({runBeforeUnload: true})`, which is CDP `Page.close` in
  Chromium.
- `page.close()`, which is CDP `Target.closeTarget` in Chromium.
- `Target.closeTarget` sent from a browser-level CDP session.
- `window.close()` in a tab the page had opened.
- `chrome.tabs.remove` from an extension, in the new headless mode. It
  closes the tab through the tab strip, as the tab's close button does.
- `context.close()` and `browser.close()`.
- In Firefox, `page.close()` either way on a tab the page had opened,
  beside its opener. Playwright's Firefox opens each page of `newPage` in
  a window of its own, so the first two closes close a page alone.

### The events

| Close | Chromium shell | Chromium | WebKit | Firefox |
|---|---|---|---|---|
| `page.close({runBeforeUnload: true})` | `beforeunload`, `pagehide`, `visibilitychange`: 75/75 | the same: 75/75 | the same: 75/75 | the same, the `pagehide` listener stopped: 75/75 |
| `page.close()` | `pagehide`, `visibilitychange`: 75/75 | the same: 75/75 | the same: 75/75 | the same, the `pagehide` listener stopped: 75/75 |
| `page.close()` either way, a tab beside its opener | – | – | – | `beforeunload` (with `runBeforeUnload`), `pagehide`, `visibilitychange`: 150/150 |
| `Target.closeTarget` | `pagehide`, `visibilitychange`: 75/75 | the same: 75/75 | – | – |
| `window.close()` | `beforeunload`, `pagehide`, `visibilitychange`: 75/75 | the same: 75/75 | the same: 75/75 | the same: 75/75 |
| `chrome.tabs.remove` | – | `beforeunload`, `pagehide`, `visibilitychange`: 75/75 | – | – |
| `context.close()` | nothing: 75/75 | `pagehide`, `visibilitychange`: 69/75 | `pagehide`, `visibilitychange`: 50/75; nothing: 13/75 | `pagehide`, `visibilitychange`, the `pagehide` listener stopped: 75/75 |
| `browser.close()` | nothing: 75/75 | nothing: 75/75 | `pagehide`, `visibilitychange`: 50/75; nothing: 7/75 | no event logged, the beacon from `visibilitychange` arrived: 74/75 |

The other runs of those two closes lost some of their logs. In a run
marked nothing, no event was logged and no request arrived whole. In 2
of Chromium's 75 `browser.close()` runs a request's headers came and its
body never did; 75 more runs, alone on the machine, had none. Firefox's
`browser.close()` ran the `visibilitychange` listener, whose beacon
arrived, but lost its keepalive fetches.

No close fired `visibilitychange` before `pagehide`, in any build. Each
close that ran the page's listeners fired `pagehide`, then
`visibilitychange` (hidden), in one task. A timer or a message queued in
`pagehide` never ran before `visibilitychange`. The two came 0.8 to 33 ms
apart (up to 7 ms in Firefox), the time the `pagehide` listener took.
Headless Chromium keeps every tab visible, a background tab too, with or
without Playwright's backgrounding switches. Every WebKit and Firefox
page was visible, a Firefox opener beside its tab too. So each close
began from a visible page. After both events, the page still ran a timer
or a message it had queued, and logged it, in 330 of 744 Chromium runs.
It did so in none of 325 WebKit runs and none of 225 Firefox runs.

Chromium's `browser.close()` and the shell's `context.close()` let
nothing through. WebKit's `context.close()` and `browser.close()` ran
the listeners in most runs, but the page's requests went down with its
network session. The server got some of them, and some bodies cut.
Firefox's `context.close()` closes each page as `page.close()` does.

### Firefox stops the first `pagehide` listener

Closing a page alone, Firefox stopped its `pagehide` listener as it
started, in every run of both `page.close` forms and `context.close()`
(225 of 225). None of that listener's requests arrived. The
`visibilitychange` listener after it ran in full. A probe page shows the
pattern. It has two `pagehide` listeners, then a `visibilitychange` and
an `unload` listener, and each loops over three items, then sends a
keepalive fetch. Ten runs a close:

| Firefox close | First `pagehide` listener | Second | `visibilitychange` and `unload` |
|---|---|---|---|
| `page.close()` either way, the page alone | stopped: 20/20 | ran: 20/20 | ran: 20/20 |
| `context.close()` | stopped: 10/10 | stopped: 4/10 | ran: 10/10 |
| `page.close()` either way or `window.close()`, a tab beside its opener | ran: 30/30 | ran: 30/30 | ran: 30/30 |

They fired in that order in every run. The same probe ran every listener
in each close of the Chromium shell and WebKit (5 runs a close), but for
the shell's `context.close()`, which runs none.

A stop comes within the listener's first statements. A first listener
whose first statement sent its keepalive fetch sent nothing (0/10). A
first listener of one assignment ran, and the stop fell on the next
listener in 3 of 10 runs (2 of 10 with `context.close()`).

The stop follows the page's content process, not its window. A popup
window closed beside its opener ran every listener (10/10). Playwright's
Firefox allows a content process per page (`dom.ipc.processCount`
60000), and a page opened by `window.open` shares its opener's. There a
page closed beside another page of its context, both from `newPage`, was
stopped too (10/10). With every page in one process
(`dom.ipc.processCount` 1), a page closed beside another page of its
context ran every listener (10/10). A page closed alone was stopped in 9
of 10. So the stop came when the closing page was the last one in its
process. A user's Firefox assigns processes by its own rules, and
whether a tab's close stops a listener there was not measured.

### What reached the server

The closes that close one page are both `page.close` forms,
`Target.closeTarget`, `window.close()` and `chrome.tabs.remove`. From
these, with bodies of 1 KiB and the three server answers pooled:

| Build | Runs | Keepalive fetches and beacons | Plain fetch, `pagehide` | Plain fetch, `visibilitychange` |
|---|---|---|---|---|
| Chromium shell | 60 | 240/240 | 59/60 | 13/60 |
| Chromium | 75 | 300/300 | 75/75 | 61/75 |
| WebKit | 45 | 180/180 | 13/45 | 5/45 |
| Firefox, the page alone | 30 | 60/120, all from `visibilitychange` | 0/30 | 0/30 |
| Firefox, a tab beside its opener | 45 | 180/180 | 0/45 | 0/45 |

No Firefox close let a plain fetch through whole. Of the 675 the page
logged as sent, at every size, 6 arrived cut and the rest not at all.

At 60 and 63 KiB, the keepalive fetch from `pagehide` arrived in every
run of these closes. The keepalive fetch from `visibilitychange` never
did: the closing page still counted the first. In Chromium and WebKit no
beacon arrived either, and the one after the first fetch returned
`false`. Firefox sent both beacons beside the fetch (180/180), since it
counts no beacon. A Firefox page alone sent nothing from `pagehide`, and
its `visibilitychange` fetch and beacon arrived (60/60 each). Plain
fetches of 60 or 63 KiB from `pagehide` arrived in 95/120 shell runs,
134/150 Chromium runs, 17/90 WebKit runs and none of 90 Firefox runs.

At 65 KiB, no keepalive fetch reached the server in any build.
`sendBeacon` returned `false` in every Chromium and WebKit run. Firefox's
returned `true`, and its 65 KiB beacons arrived (149 of 150). The plain
fetches, from `pagehide` and `visibilitychange` together:

| Server | Chromium shell | Chromium | WebKit | Firefox |
|---|---|---|---|---|
| Answers at once | 34 whole, 6 none | 49 whole, 1 none | 10 whole, 3 cut, 17 none | 40 none |
| Holds the answer | 37 whole, 3 none | 49 whole, 1 none | 4 whole, 5 cut, 21 none | 40 none |
| Leaves the body unread | 36 whole, 4 none | 49 whole, 1 none | 7 whole, 6 cut, 17 none | 40 none |

WebKit cut its 65 KiB bodies at 64,844 to 64,892 bytes. A plain body of
1 MiB that the server read at once came whole from Chromium in 130 of
180 sends. It was cut in 43, 41 of them at 192 or 256 KiB, and not sent
in 7. Left unread, it was cut every time it came (88 of 90), at 80 to
144 KiB. WebKit never sent a whole 1 MiB body: it cut 50 of 90 at 63 to
511 KiB and sent nothing for the rest. Firefox never did either: 6 came
cut, from `pagehide` beside the opener. Its `sendBeacon` took 1 MiB
bodies, and each one sent beside the opener came whole (90/90). Of the
60 sent from a page alone, `context.close()` and `browser.close()`, 8
came whole and 26 cut.

### A slow server and a slow connection

With the answer held, every keepalive fetch and beacon of these closes
kept its connection and took its answer 2 s later (120/120 in the
shell, 150/150 in Chromium, 90/90 in WebKit). So did each of the 281
that came whole in Firefox. The browsers hung up on every plain fetch
before its answer, 13 to 389 ms after the close. The median was 44 ms in the
shell, 55 ms in Chromium and 44 ms in WebKit. Firefox's plain fetches
never came whole.

In Chromium a body left unread for 2 s still came whole up to 65 KiB:
Windows had taken it into the socket buffers before the browser hung
up, and only its 1 MiB bodies were cut. WebKit cut its 65 KiB bodies
whatever the server did.

For a slow connection, a SOCKS5 proxy in front of the server answered
each new connection after 50 or 200 ms, as a new connection over a real
network waits for its handshakes. The browser cannot send the request
before then. Every keepalive fetch and beacon still arrived, and almost
no plain fetch did:

| Build | Runs per delay | Keepalive fetches and beacons, each delay | Plain, 50 ms | Plain, 200 ms |
|---|---|---|---|---|
| Chromium shell | 40 | 100/100 | 0/80 | 0/80 |
| Chromium | 50 | 125/125 | 0/100 | 2/100 |
| WebKit | 30 | 75/75 | 0/60 | 0/60 |
| Firefox, the page alone | 20 | 40/40 | 0/20 | 0/20 |
| Firefox, a tab beside its opener | 30 | 105/105 | 0/60 | 0/60 |

The browsers cancelled the plain fetches while their connections were
still opening. Chromium let the proxy finish those connections and
wrote nothing on them. WebKit closed them 12 to 20 ms after the close.
Firefox asked the proxy for no connection for them: each of its
connections to the server carried a request.

### Aborting on going hidden

One way stayed open: if `visibilitychange` came a task before
`pagehide`, an abort on going hidden could free Chromium's room in
time. The page sent A (50 KiB with keepalive, held by the server)
while visible. In `visibilitychange` it aborted A and queued a timer. In
`pagehide`, and in that timer, it sent B (30 KiB with keepalive) and B's
plain copy. Each close ran 5 times:

- B from `pagehide` was refused in every close but `window.close()`,
  since the abort came after it.
- `window.close()` let B go in both Chromium builds and WebKit (15/15):
  the browser had stopped counting A before `pagehide`. Chromium failed
  A's fetch with a `TypeError` there, and WebKit gave the page no sign.
- B from the timer arrived in 20/20 runs of the new headless mode's
  other page closes and in 2/15 of the shell's. In WebKit and Firefox
  the timer never ran.
- B's plain copy from `pagehide` arrived in 18/20 shell runs, 23/25
  Chromium runs, 4/15 WebKit runs and 0/15 Firefox runs.
- Firefox let no B through from any close (0/35). Its `window.close()`
  still counted A at `pagehide`, and the abort closed A's connection.

So an abort on going hidden lands after `pagehide` has sent everything,
and a timer after it is no surer than the plain copy.

### The ledger's rules in a page that lives

Each check ran 5 times (the release checks 10, with the answer's body
sent at once or held). Both Chromium builds and WebKit agreed, and
Firefox differed from them only on beacons:

| Request | Chromium and WebKit | Firefox |
|---|---|---|
| A body of 65,536 bytes, alone | sent | sent |
| 65,537 bytes, alone | refused | refused |
| 1 byte, beside two 32 KiB requests on their way | refused | refused |
| No body, or an empty string, beside them | sent | sent |
| 21,845 `€` (65,535 bytes of UTF-8); 21,846 `€` (65,538 bytes) | sent; refused | sent; refused |
| 65,536 bytes with an 8 KiB query string | sent | sent |
| 8 KiB once a 60 KiB request's answer headers are in, its body held 800 ms | refused | refused |
| 8 KiB once that answer's `clone().arrayBuffer()` has resolved: in its continuation, a microtask, a task or 50 ms later | sent | sent |
| An 8 KiB beacon beside a 60 KiB keepalive fetch; an 8 KiB keepalive fetch beside a 60 KiB beacon | refused; refused | sent; sent |
| A 4 KiB beacon beside a 60 KiB keepalive fetch | sent | sent |
| A 65 KiB beacon, alone | refused (`false`) | sent (`true`) |
| 60 KiB from a second page while the first holds 60 KiB | sent | sent |

When the answer's 2-byte body came with its headers, WebKit took the
8 KiB at the headers in 2 of 5 runs: that body was already in.

A page also has a cap on the number of keepalive requests in flight,
with bodies or without. A page started 256 or 257 bodiless keepalive
POSTs in one task, each held 200 ms by the server. Both Chromium builds
and Firefox sent 256 and refused the 257th, 5 runs each. WebKit sent all
257.

## The app at a close

The app ran on dev at `73fd244c`, built and served by the browser
suite's harness on an isolated backend. The engines were Playwright's
Chromium headless shell, WebKit and Firefox, and each tab was alone in
its browser context.

### Its `pagehide` listeners

The app registers six `pagehide` listeners, on a notes page and on a
paper alike, in this order:

1. `journalAll` (`ink/inkStore.js`, as the module loads) writes every
   dirty drawing to the draft journal.
2. `flushSession` (`app/sessionState.js`, as the module loads) writes the
   viewer's layout to `localStorage`.
3. `flushReadPos` (App) sends the reading position's `PUT`.
4. The profile's flush (`app/prefs.js` `useProfileSync`) sends its
   `PATCH`.
5. `onHide` (`collaboration/usePageCollab.js`) sends the queued edits'
   `POST`.
6. `leave` (`ink/useInkSave.js`) flushes the handwriting.

An Inbox mark goes with keepalive when it is made, not from `pagehide`.
A probe wrapped each listener to report its start and its end with
bodiless keepalive requests to a second origin. It ran twice on a notes
page and twice on a paper for each close:

| Close | Chromium shell | WebKit | Firefox |
|---|---|---|---|
| `page.close()` | all six ran: 4/4 | all six ran: 4/4 | `journalAll` stopped before its first statement, the other five ran: 4/4 |
| `context.close()` | none ran: 4/4 | all six ran: 4/4 | as `page.close()`: 4/4 |

### What a close right after a change keeps

`frontend/tests/e2e/tabClose.mjs` makes a change in a fresh browser
context and closes it at once:

- A note gets text typed at its end, inside the edits' 350 ms debounce.
- A paper gets a stroke, with its tab closed as the pen lifts or 400 ms
  later. The journal writes a drawing 300 ms after the edit, and the save
  goes at 700 ms.
- A setting (Dark PDF pages) is turned on in Settings, inside the
  profile's 1 s push delay.

A stroke the server lacks after a `page.close()` gets a next visit in the
same context, which may restore it from the journal. Each case ran 5
times, one engine at a time:

| Change | Close | Chromium shell | WebKit | Firefox |
|---|---|---|---|---|
| A note typed | `page.close()` | saved 5/5 | saved 5/5 | saved 5/5 |
| | `context.close()` | lost 5/5 | saved 5/5 | lost 5/5 |
| A paper's first stroke, closed as the pen lifts | `page.close()` | lost 5/5 | lost 5/5 | lost 5/5 |
| | `context.close()` | lost 5/5 | lost 5/5 | lost 5/5 |
| A stroke added to a saved group, closed as the pen lifts | `page.close()` | lost 5/5 | lost 5/5 | saved 5/5 |
| | `context.close()` | lost 5/5 | lost 5/5 | lost 5/5 |
| A paper's first stroke, closed 400 ms after the pen lifts | `page.close()` | saved 5/5 | saved 5/5 | saved 5/5 |
| | `context.close()` | lost 5/5 | saved 5/5 | lost 5/5 |
| A setting changed | `page.close()` | saved 5/5 | saved 5/5 | saved 5/5 |
| | `context.close()` | lost 5/5 | saved 5/5 | lost 5/5 |

After a `page.close()` the journal held none of the strokes closed as the
pen lifts (0/30) and every stroke closed 400 ms later (15/15). No lost
stroke came back on the next visit. The server's log showed why:

- Chromium's shell sent nothing at `context.close()`. Firefox sent every
  request without the session cookie. Each was answered 409, since it
  named the tab's account (`X-Gamma-User`) and came signed out.
- A paper's first stroke, closed as the pen lifts by `page.close()`:
  Chromium and WebKit sent the group's insert and the upload, and no
  `PUT` (10/10). The drawing had no name yet, since `nameLater` waits for
  the page to idle. So its `PUT` waited for the upload's answer, which
  the closed page never read.
- Firefox named the drawing in time and sent the `PUT` beside the upload.
  The `PUT` reached the server before the group's insert and was answered
  404 (5/5).
- A stroke added to a saved group: Chromium and WebKit sent the upload
  alone (10/10), for the same reason. Firefox sent the upload and the
  `PUT`, and both landed (5/5).
- Closed 400 ms after the pen lifts, every engine sent the upload and
  the `PUT` side by side and saved the stroke at `page.close()`. In one
  later Chromium run the drawing still had no name at 400 ms. The
  journal, written at 300 ms, brought the stroke back on the next visit.

### The journal's write at a close

A close before the journal's 300 ms write leaves the write to
`pagehide`: `journalAll`, and `markSending` before a `PUT` sent beside its
upload.

A probe page opened an IndexedDB database at load. Its second
`pagehide` listener, after one of a single assignment, put one record
each way. One went at once on the open connection and one after a
microtask, as the journal does. Both went again followed by
`IDBTransaction.commit()`. The next page of the context read the store.
Five runs each of a page closed beside another page of its context, the
same with `runBeforeUnload`, and a page alone:

| Put | Chromium shell | WebKit | Firefox |
|---|---|---|---|
| In `pagehide`, no `commit()`, at once or after a microtask | landed 15/15 | 0/15 | 0/13 |
| In `pagehide`, then `commit()` | 15/15 | 0/15 | 13/13 |
| In `visibilitychange`, no `commit()` | 7/15 | 0/15 | 0/15 |

In Firefox the listener's own keepalive marker arrived in 13 of the 15
runs: the stop fell on it in the other 2. In the app, a second probe
logged every put on the journal's store, three runs a case, the tab
closed as the pen lifts. In another three it called `commit()` after
each put:

- A stroke added to a saved group: a put went from `pagehide` in every
  run. It came from `journalAll` in Chromium and WebKit, and from the
  flush's `markSending` in Firefox.
- None of these puts landed (0/9). With `commit()` the entry landed in
  Chromium, and the next visit restored and saved the stroke (3/3). It
  landed in Firefox (3/3) and not in WebKit (0/3).
- A paper's first stroke: the journal's database opens when the group's
  block first shows, and the open had not completed in Chromium or WebKit
  (0/6 each). No put went.
- Firefox's database was open, and with `commit()` its entry landed
  (3/3). The next visit restored nothing (3/3): the restore counts the
  entry's sent upload, which the server has, as landed. The block names
  no file, so it took the stroke for one deleted since.

The probe page's Chromium result did not hold in the app. After a
stroke added to a saved group, closed as the pen lifts, the journal held
nothing in 8 of 8 Chromium closes (3 here, 5 in the table above). In an
earlier round, its engines run side by side, it held the stroke after 7
of 8 Chromium closes made 0 or 150 ms after the pen lifted.

### With every sender on both events

The fifth round, the same day, closed the app built with what followed
from these rounds ([dev/collab.md](../dev/collab.md#keepalive),
[dev/handwriting.md](../dev/handwriting.md) "Fences"):

- Every sender sends from `pagehide` and again from `visibilitychange`
  (hidden), and marks its data sent only once the request has gone.
- The edits' `hide()` sends the queue's next batch through the session
  with keepalive and the batches behind it as copies, all under the ids
  they keep until answered. It runs once a task.
- The draft journal opens its database as the page loads. It writes a
  draft at once as it changes, an eraser pass 300 ms after its last
  frame. Each write's transaction starts in the change's own task and is
  committed as it is made.
- A journal entry whose block names no file comes back as it is: none of
  its `PUT`s landed.

`tabClose.mjs` ran each case 5 times, one engine at a time, closed by
`page.close()` and by `context.close()`. It ran 5 times more with no
`pagehide` listener of the app: `--stop pagehide`, an init script that
drops each one as the app adds it, as if each were stopped before its
first statement. Saved means the server had the change after the close.
Journal means the next visit restored the stroke from the draft journal
and saved it.

| Change | Close | Chromium shell | WebKit | Firefox |
|---|---|---|---|---|
| A note typed | `page.close()` | saved 5/5 | saved 5/5 | saved 5/5 |
| | no `pagehide` listener | saved 5/5 | saved 5/5 | saved 5/5 |
| | `context.close()` | lost 5/5 | saved 5/5 | lost 5/5 |
| A paper's first stroke, closed as the pen lifts | `page.close()` | journal 5/5 | journal 5/5 | journal 5/5 |
| | no `pagehide` listener | journal 5/5 | journal 5/5 | journal 5/5 |
| | `context.close()` | lost 5/5 | lost 5/5 | lost 5/5 |
| A stroke added to a saved group, closed as the pen lifts | `page.close()` | journal 5/5 | journal 5/5 | saved 5/5 |
| | no `pagehide` listener | journal 5/5 | journal 5/5 | journal 5/5 |
| | `context.close()` | lost 5/5 | lost 5/5 | lost 5/5 |
| A paper's first stroke, closed 400 ms after the pen lifts | `page.close()` | saved 4/5, journal 1/5 | saved 5/5 | saved 5/5 |
| | no `pagehide` listener | journal 5/5 | journal 5/5 | journal 5/5 |
| | `context.close()` | lost 5/5 | saved 5/5 | lost 5/5 |
| A setting changed | `page.close()` | saved 5/5 | saved 5/5 | saved 5/5 |
| | no `pagehide` listener | saved 5/5 | saved 5/5 | saved 5/5 |
| | `context.close()` | lost 5/5 | saved 5/5 | lost 5/5 |

Every `page.close()` kept its change, 75 of 75, and so did every close
with no `pagehide` listener, 75 of 75. The journal held the stroke after
each of those closes (90/90). The server's log showed the rest:

- A stroke closed as the pen lifts went out as in the fourth round.
  Chromium and WebKit sent the upload and no `PUT`, the drawing not named
  yet. Firefox sent a paper's first stroke's `PUT` beside the upload, and
  it reached the server before the group's insert (404, 5/5). The next
  visit restored each from the journal and saved it. Firefox's `PUT` of a
  stroke added to a saved group landed beside its upload, as before.
- Closed 400 ms after the pen lifts, one Chromium drawing had no name
  yet, so its `PUT` waited for the upload's answer. The journal brought
  it back.
- With no `pagehide` listener the edits, the profile and the journal went
  from `visibilitychange` alone. Handwriting's flush there sent each
  upload and left its `PUT` for the answer, which a closed page never
  reads. So every stroke came back from the journal, the one closed at
  400 ms too.
- `context.close()` lost what it lost in the fourth round. Chromium's
  shell ran no listener, and Firefox sent every request without the
  session cookie (409). WebKit kept the note, the setting and the stroke
  closed at 400 ms. A stroke closed as the pen lifts went with the
  context's journal.

The app at `73fd244c`, closed with no `pagehide` listener (3 runs a
case), shows what the scenario catches. It lost the note in Firefox (3/3),
its edits going from `visibilitychange` as a plain fetch, and in WebKit
(1/3). It lost every stroke closed as the pen lifts, in all three engines
(18/18). It kept the setting (9/9), whose flush on going hidden went with
keepalive. It kept the stroke closed at 400 ms (9/9), which the journal
had written at 300 ms.

### A tab switch with a save waiting

The fifth round's build sent the edits from going hidden as from a close:
the next batch through the session and the batches behind it as copies, at
once. A probe tried that on a tab switch, the same day, on the browser
suite's harness and the app at `66e006f4`:

- A note held "hello". A route answered its save's first `POST` with 503,
  as a restarting server would, so the batch waited for its retry.
- " world" was typed before that `POST`, and six Backspaces took it back.
  That edit queued behind the waiting batch.
- The tab went hidden, and visible 300 ms later. The probe dispatched
  `visibilitychange` with `document.visibilityState` overridden, since a
  headless tab never hides.
- A route held the first `POST` after the switch for 150 ms, as a slow
  connection might.

The server's text once the saves were answered:

| Build | Chromium shell | WebKit | Firefox |
|---|---|---|---|
| `73fd244c`, a flush on going hidden | "hello" 4/4 | – | – |
| `66e006f4` | "hello world" 4/4 | "hello world" 4/4 | "hello world" 4/4 |
| `66e006f4`, no `POST` held | "hello" 12/12 | – | – |
| Going hidden sends the next batch alone | "hello" 4/4 | "hello" 4/4 | "hello" 4/4 |

At `66e006f4` the copy of the edit behind the waiting batch landed first
and changed nothing. The waiting batch landed after it and put " world"
back. The edit's own send was then a replay, answered from the log. So the
server kept the word the tab had taken back, and the tab counted its edits
saved. Without the hold the waiting batch, sent first, landed first.

So going hidden sends the next batch alone (`hide(false)`), and the rest
follows its answer, in order. Only `pagehide` sends copies behind a batch.
Going hidden in the same task sends nothing more after a `pagehide` that
sent everything, and all again after one stopped part way. A close whose
edits' `pagehide` listener Firefox stopped before its first statement sends
just the next batch then. In the app's closes Firefox stopped only
`journalAll`. The suite's collab group has the probe as a step, which fails
at `66e006f4` in all three engines.

`tabClose.mjs` ran on the build with that change, 3 runs a case, one
engine at a time, by `page.close()` and with `--stop pagehide`. Every case
kept its change, 90 of 90, and the journal held every stroke (54/54). The
outcomes matched the fifth round's, case by case.

## What follows

- The ledger never aborts a request to make room for another. No build
  frees an aborted request's share in the task that aborts it, and
  Firefox ends the request.
- A critical request that lacks room only because lower priorities hold
  it goes both ways at once: with keepalive and as the plain copy it
  would have been otherwise. Its batch id makes whichever copy lands
  second a replay.
- A page leaving for another lets the keepalive copy through in Chromium
  and WebKit. Firefox refuses it there, and the plain copy went in every
  navigation and reload.
- A closing tab with a drawing's upload still on its way stays a
  residual risk: there the edits' plain copy is all that goes, except
  after `window.close()` in Chromium and WebKit.
- That copy reached a local server from Chromium in most closes, from
  WebKit in few and from Firefox never. Once a new connection took 50
  or 200 ms to open, it almost never did (2 of 640).
- The ledger's rules match all three engines: the 64 KiB includes its
  last byte and counts a body's UTF-8 bytes, not its URL. Each page has
  its own 64 KiB.
- A request without a body takes none of the 64 KiB, but Chromium and
  Firefox refuse a page's 257th keepalive request in flight, a body or
  none. The app's close sends far fewer.
- In every build a request counts until its answer's body is in. It has
  stopped counting by the time `clone().arrayBuffer()` resolves, where
  the ledger releases it.
- In Chromium and WebKit a beacon takes from the same 64 KiB, and
  Firefox counts no beacon. The app sends none, and a beacon sent
  outside the ledger would take room the ledger does not count.
- A tab closed while visible fires `pagehide` first and
  `visibilitychange` after it, in one task, in every build.
  `visibilitychange` comes first only for a tab hidden before it closes.
- So at a close every sender of the app sends from `pagehide`, and from
  `visibilitychange` sends again only what a stopped `pagehide` listener
  left. The ledger needs neither order.
- Nothing queued in `pagehide` is sure to run: WebKit and Firefox ran no
  task after the two events. A request the page needs at a close starts
  in the listener itself, as the app's do, except a handwriting `PUT`
  that waits for its upload (a draft not named yet, or one the server
  will merge). The draft journal keeps that drawing: it writes a draft
  as it changes, while the page lives ("The app at a close").
- Firefox can stop a `pagehide` listener before it sends: closing the
  last page of a content process stopped the first one, and the second
  in 4 of 10 `context.close()` runs. The listeners after a stop ran in
  full.
- In the app the stop falls on `journalAll`, the first of its six
  `pagehide` listeners. Each sender also sends from `visibilitychange`,
  and marks its data sent only once the request has gone, so a stop of
  any of them loses nothing. The edits' batches behind an unanswered one
  are the exception: `visibilitychange` leaves them to its answer. With
  no `pagehide` listener of the app running, every change a
  `page.close()` followed was kept in all three engines ("With every
  sender on both events", "A tab switch with a save waiting").
- Copies of the edits' batches sent at once can land out of order. One
  that applies before the batch it follows is never undone, since its
  in-order send is a replay. So only `pagehide` sends them at once, and
  going hidden sends the next batch, the rest after its answer.
- A note typed or a setting changed just before `page.close()` reached
  the server in every run of every engine.
- At `73fd244c` a stroke drawn as the tab closed was lost in all three
  engines when it was a paper's first, and in Chromium and WebKit when it
  joined a saved group. A stroke 400 ms old was saved by every
  `page.close()`. With the journal written as a draft changes, none was
  lost: each was saved by the close or came back on the next visit.
- An uncommitted journal write made at a close does not land. Firefox and
  WebKit never kept it, and Chromium kept it in one of three rounds. The
  journal commits each write as it is made.
- `commit()` lands a put from `pagehide` in Chromium and Firefox. In
  Firefox it must come from a listener after the first, and WebKit keeps
  only what the journal wrote while the page lived.
- So in WebKit the journal holds a close's drawing only when it wrote
  each change at once. A write put off for 300 ms after a change, to
  coalesce changes, left an older drawing in WebKit's journal in the
  suite: an undo and a redo, then a reload. The reload's flush saved the
  redone drawing, the same file as the entry's base, and the next page
  restored the undone one as unsaved work.
- And soon enough. A paper's first stroke at first waited behind a delete
  of the group's entry, which the tab had never written, and behind the
  render the stroke caused: its transaction started from a microtask
  after that render. Closed as the pen lifts, WebKit lost 2 of 20. With
  the transaction started in the stroke's own task and no delete before
  it, 40 of 40 were kept.
- A `PUT` sent beside its upload can reach the server before the group's
  insert, which the edits' `POST` carries. A 404 at a close is never
  retried. The block then names no file, which the journal's restore
  takes for one that took none of the entry's `PUT`s.
- `context.close()` stands for no user's close. Chromium's shell runs no
  listener there, and Firefox sends the page's requests without the
  session cookie.

## Still open

- Safari, and the app's WKWebView on a device. The Windows WebKit shares
  WebCore's keepalive accounting but not Apple's network stack.
- A user closing a tab in a headed Chrome. Headless Chromium never hides
  a tab, so it cannot show whether a headed one hides the closing tab
  before `pagehide` (the tab strip's own close fired `pagehide` first).
- A user's Firefox, headed and with its own process rules: whether
  closing a tab there stops its first `pagehide` listener, or another.
  The app's senders hold whichever one it stops, but for the edits'
  batches behind an unanswered one ("With every sender on both events").
- A close or a reload with a batch unanswered and edits behind it still
  sends them at once, and they can land out of order. Keeping their order
  would take a server that holds a batch until the one it follows lands.
- Timing. Whether a drawing is named before the close varied between
  rounds. So does whether a stroke drawn as the tab closes is saved by the
  close or brought back by the journal.
- A user quitting the browser: Playwright's `browser.close()` ran no
  listener in Chromium, so it stands for nothing a user does.
- A real network. The proxy delayed only a connection's setup, not a
  slow upload, loss, a connection kept open from earlier requests or
  HTTP/2.
