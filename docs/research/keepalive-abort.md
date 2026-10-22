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
on ("Every close the automation offers").

## An abort frees nothing in time

A Node server on a free port held a request: it read the body and did
not answer until told to. In a fresh browser context for each run, the
page sent a 50 KiB keepalive POST (A), which the server held, waited
300 ms for its body to arrive, then sent a 30 KiB keepalive POST (B).
Together they come to 80 KiB, so B is refused while A counts. Each run
aborts A through the `AbortController` its fetch was given, then sends B
at a different moment:

| B sent | Chromium | WebKit |
|---|---|---|
| A not aborted | refused | refused |
| right after `abort()` | refused | refused |
| after a microtask | refused | refused |
| after A's `AbortError` was handled | refused | refused |
| in the event listener that aborted A | refused | refused |
| a task later (`setTimeout` 0) | sent | refused |
| 50 ms or 500 ms later | sent | refused |
| once the server answered A | sent | sent |
| A's headers in and its body held, aborted, B at once | refused | refused |

A refused B is a network error (`TypeError: Failed to fetch`, `Load
failed`) and never reaches the server. Neither browser stopped an aborted
keepalive request on the wire: the server's socket for A stayed open for
the 3 s watched, while an aborted plain request's socket closed (at once
in Chromium, within a second in WebKit). An abort only rejects the
caller's promise with an `AbortError`. Chromium stops counting the
request's bytes one task later, WebKit only once its answer is in, and in
both engines the request carries on and may land. So a keepalive sent in
the task that aborted A is refused, and a ledger that freed A's bytes to
send the edits with keepalive would lose them where a plain fetch might
have landed.

## What a real pagehide lets through

The same held A, then the page went away. Its `pagehide` listener sent
a 1-byte keepalive marker, aborted A or not, sent B with keepalive (and a
plain copy C once B was refused), and sent a plain POST D of its own:

| The page | Chromium | WebKit |
|---|---|---|
| navigates away, A aborted or not | B sent, D sent | B sent, D lost |
| reloads | B sent, D sent in one run of two | B sent, D lost |
| closes (`page.close`), A aborted or not | B refused, C and D sent | B refused, C and D lost |

A page that leaves for another (a navigation, a reload) stops counting
its earlier keepalive requests, in both engines: B went with
keepalive beside the held A, where a ledger counting A would have sent it
plain. That plain copy was lost in WebKit and in one Chromium reload. A
closing tab counts A whatever the page does, and refuses B.

## Every close the automation offers

Measured on 21 October 2026 on Windows 11, headless only, with
Playwright 1.63.0:

| Build | Version | Playwright's build |
|---|---|---|
| Chromium headless shell, Playwright's default headless | 153.0.8010.12 | `chromium_headless_shell-1243` |
| Chromium in its new headless mode (`channel: "chromium"`) | 153.0.8010.12 | `chromium-1243` |
| WebKit | 26.6 | `webkit-2359` |

The server was Python's `ThreadingHTTPServer` on 127.0.0.1. For each
request it logged when it came, its body's bytes, whether the body came
whole, when the client hung up and whether the answer could be written.
It answered at once, or read the body at once and held the answer 2 s,
or left the body unread for 2 s on a port with a 4 KiB receive buffer.
In `pagehide` and in `visibilitychange` (hidden), the page sent a
keepalive fetch, a plain fetch and a `sendBeacon`, in that order. Each
carried a body of 1, 60, 63 or 65 KiB, or 1 MiB. The page logged each
event to a second origin with bodiless keepalive POSTs, which take none
of the 64 KiB. Each case ran 5 times, the three builds side by side.

The closes:

- `page.close({runBeforeUnload: true})`, which is CDP `Page.close` in
  Chromium.
- `page.close()`, which is CDP `Target.closeTarget` in Chromium.
- `Target.closeTarget` sent from a browser-level CDP session.
- `window.close()` in a tab the page had opened.
- `chrome.tabs.remove` from an extension, in the new headless mode. It
  closes the tab through the tab strip, as the tab's close button does.
- `context.close()` and `browser.close()`.

### The events

| Close | Chromium shell | Chromium | WebKit |
|---|---|---|---|
| `page.close({runBeforeUnload: true})` | `beforeunload`, `pagehide`, `visibilitychange`: 75/75 | the same: 75/75 | the same: 75/75 |
| `page.close()` | `pagehide`, `visibilitychange`: 75/75 | the same: 75/75 | the same: 75/75 |
| `Target.closeTarget` | `pagehide`, `visibilitychange`: 75/75 | the same: 75/75 | – |
| `window.close()` | `beforeunload`, `pagehide`, `visibilitychange`: 75/75 | the same: 75/75 | the same: 75/75 |
| `chrome.tabs.remove` | – | `beforeunload`, `pagehide`, `visibilitychange`: 75/75 | – |
| `context.close()` | nothing: 75/75 | `pagehide`, `visibilitychange`: 69/75 | `pagehide`, `visibilitychange`: 50/75; nothing: 13/75 |
| `browser.close()` | nothing: 75/75 | nothing: 75/75 | `pagehide`, `visibilitychange`: 50/75; nothing: 7/75 |

The other runs of those two closes lost some of their logs. In a run
marked nothing, no event was logged and no request arrived whole. In 2
of Chromium's 75 `browser.close()` runs a request's headers came and its
body never did; 75 more runs, alone on the machine, had none.

No close fired `visibilitychange` before `pagehide`. Each close that ran
the page's listeners fired `pagehide`, then `visibilitychange`
(hidden), in one task. A timer or a message queued in `pagehide` never
ran before `visibilitychange`. The two came 0.8 to 33 ms apart, the time
the `pagehide` listener took. Headless Chromium keeps every tab visible,
a background tab too, with or without Playwright's backgrounding
switches, and every WebKit page was visible. So each close began from a
visible page. After both events, the page still ran a timer or a message
it had queued, and logged it, in 330 of 744 Chromium runs. It did so in
none of 325 WebKit runs.

Chromium's `browser.close()` and the shell's `context.close()` let
nothing through. WebKit's `context.close()` and `browser.close()` ran
the listeners in most runs, but the page's requests went down with its
network session. The server got some of them, and some bodies cut.

### What reached the server

The closes that close one page are both `page.close` forms,
`Target.closeTarget`, `window.close()` and `chrome.tabs.remove`. From
these, with bodies of 1 KiB and the three server answers pooled:

| Build | Runs | Keepalive fetches and beacons | Plain fetch, `pagehide` | Plain fetch, `visibilitychange` |
|---|---|---|---|---|
| Chromium shell | 60 | 240/240 | 59/60 | 13/60 |
| Chromium | 75 | 300/300 | 75/75 | 61/75 |
| WebKit | 45 | 180/180 | 13/45 | 5/45 |

At 60 and 63 KiB, the keepalive fetch from `pagehide` arrived in every
run of these closes. The beacon after it returned `false`, and the
keepalive fetch and the beacon from `visibilitychange` never arrived:
the closing page still counted the first fetch. Plain fetches of 60 or
63 KiB from `pagehide` arrived in 95/120 shell runs, 134/150 Chromium
runs and 17/90 WebKit runs.

At 65 KiB, no keepalive fetch reached the server, and `sendBeacon`
returned `false`, in every run. The plain fetches, from `pagehide` and
`visibilitychange` together:

| Server | Chromium shell | Chromium | WebKit |
|---|---|---|---|
| Answers at once | 34 whole, 6 none | 49 whole, 1 none | 10 whole, 3 cut, 17 none |
| Holds the answer | 37 whole, 3 none | 49 whole, 1 none | 4 whole, 5 cut, 21 none |
| Leaves the body unread | 36 whole, 4 none | 49 whole, 1 none | 7 whole, 6 cut, 17 none |

WebKit cut its 65 KiB bodies at 64,844 to 64,892 bytes. A plain body of
1 MiB that the server read at once came whole from Chromium in 130 of
180 sends. It was cut in 43, 41 of them at 192 or 256 KiB, and not sent
in 7. Left unread, it was cut every time it came (88 of 90), at 80 to
144 KiB. WebKit never sent a whole 1 MiB body: it cut 50 of 90 at 63 to
511 KiB and sent nothing for the rest.

### A slow server and a slow connection

With the answer held, every keepalive fetch and beacon of these closes
kept its connection and took its answer 2 s later (120/120 in the
shell, 150/150 in Chromium, 90/90 in WebKit). The browsers hung up on
all but one plain fetch before its answer, 13 to 389 ms after the close.
The median was 44 ms in the shell, 55 ms in Chromium and 44 ms in
WebKit.

A body left unread for 2 s still came whole up to 65 KiB. Windows had
taken it into the socket buffers before the browser hung up. Only the
1 MiB bodies were cut.

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

The browsers cancelled the plain fetches while their connections were
still opening. Chromium let the proxy finish those connections and
wrote nothing on them. WebKit closed them 12 to 20 ms after the close.

### Aborting on going hidden

One way stayed open: if `visibilitychange` came a task before
`pagehide`, an abort on going hidden could free Chromium's room in
time. The page sent A (50 KiB with keepalive, held by the server)
while visible. In `visibilitychange` it aborted A and queued a timer. In
`pagehide`, and in that timer, it sent B (30 KiB with keepalive) and B's
plain copy. Each close ran 5 times:

- B from `pagehide` was refused in every close but `window.close()`,
  since the abort came after it.
- `window.close()` let B go in all three builds (15/15): the browser
  had stopped counting A before `pagehide`. Chromium failed A's fetch
  with a `TypeError` there, and WebKit gave the page no sign.
- B from the timer arrived in 20/20 runs of the new headless mode's
  other page closes and in 2/15 of the shell's. In WebKit the timer
  never ran.
- B's plain copy from `pagehide` arrived in 18/20 shell runs, 23/25
  Chromium runs and 4/15 WebKit runs.

So an abort on going hidden lands after `pagehide` has sent everything,
and a timer after it is no surer than the plain copy.

### The ledger's rules in a page that lives

Each check ran 5 times (the release checks 10, with the answer's body
sent at once or held), and the three builds agreed:

| Request | Result |
|---|---|
| A body of 65,536 bytes, alone | sent |
| 65,537 bytes, alone | refused |
| 1 byte, beside two 32 KiB requests on their way | refused |
| No body, or an empty string, beside them | sent |
| 21,845 `€` (65,535 bytes of UTF-8); 21,846 `€` (65,538 bytes) | sent; refused |
| 65,536 bytes with an 8 KiB query string | sent |
| 8 KiB once a 60 KiB request's answer headers are in, its body held 800 ms | refused |
| 8 KiB once that answer's `clone().arrayBuffer()` has resolved: in its continuation, a microtask, a task or 50 ms later | sent |
| An 8 KiB beacon beside a 60 KiB keepalive fetch; an 8 KiB keepalive fetch beside a 60 KiB beacon | refused; refused |
| A 4 KiB beacon beside a 60 KiB keepalive fetch | sent |
| A 65 KiB beacon, alone | refused (`false`) |
| 60 KiB from a second page while the first holds 60 KiB | sent |

When the answer's 2-byte body came with its headers, WebKit took the
8 KiB at the headers in 2 of 5 runs: that body was already in.

## What follows

- The ledger never aborts a request to make room for another.
- A critical request that lacks room only because lower priorities hold
  it goes both ways at once: with keepalive, which a page leaving for
  another lets through, and as the plain copy it would have been
  otherwise. Its batch id makes whichever copy lands second a replay.
- A closing tab with a drawing's upload still on its way stays a
  residual risk: there the edits' plain copy is all that goes, except
  after `window.close()`. That copy reached a local server from Chromium
  in most closes, from WebKit in few, and almost never once a new
  connection took 50 or 200 ms to open (2 of 480).
- The ledger's rules match both engines: the 64 KiB includes its last
  byte, counts a body's UTF-8 bytes and not its URL, and never refuses a
  request without a body. Each page has its own 64 KiB.
- A request counts until its answer's body is in. It has stopped
  counting by the time `clone().arrayBuffer()` resolves, where the
  ledger releases it.
- A beacon takes from the same 64 KiB. The app sends none, and a beacon
  sent outside the ledger would take room the ledger does not count.
- A tab closed while visible fires `pagehide` first and
  `visibilitychange` after it, in one task. `visibilitychange` comes
  first only for a tab hidden before it closes.
- So at a close the edits, handwriting, the profile and the reading
  position all go from `pagehide`, and `visibilitychange` finds nothing
  new to send. The ledger needs neither order.
- Nothing queued in `pagehide` is sure to run: WebKit ran no task after
  the two events. A request the page needs at a close starts in the
  listener itself, as the app's do.

## Still open

- Firefox, which was not measured: Playwright's Firefox is not installed
  here, and its download (about 100 MB) was not approved.
- Safari, and the app's WKWebView on a device. The Windows WebKit shares
  WebCore's keepalive accounting but not Apple's network stack.
- A user closing a tab in a headed Chrome. Headless Chromium never hides
  a tab, so it cannot show whether a headed one hides the closing tab
  before `pagehide` (the tab strip's own close fired `pagehide` first).
- A user quitting the browser: Playwright's `browser.close()` ran no
  listener in Chromium, so it stands for nothing a user does.
- A real network. The proxy delayed only a connection's setup, not a
  slow upload, loss, a connection kept open from earlier requests or
  HTTP/2.
