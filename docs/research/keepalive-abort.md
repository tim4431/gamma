# Aborting a keepalive request to make room for another

The question: the page's keepalive ledger gives the queued page edits
first claim on the 64 KiB of keepalive bodies a page may have in flight,
but a handwriting upload sent with keepalive when the tab was last hidden
can still be on its way when the tab closes, with the edits grown past
the room it leaves them. Can the ledger abort that upload so the edits'
POST goes with keepalive in its place? Measured in October 2026 in
Playwright 1.63's headless Chromium 153 and WebKit 26.6 on Windows; what
the ledger does instead is in [dev/collab.md](../dev/collab.md#keepalive).

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

## What follows

- The ledger never aborts a request to make room for another.
- A critical request that lacks room only because lower priorities hold
  it goes both ways at once: with keepalive, which a page leaving for
  another lets through, and as the plain copy it would have been
  otherwise. Its batch id makes whichever copy lands second a replay.
- A closing tab with a drawing's upload still on its way stays a
  residual risk. There the edits' plain copy is all that goes, and it
  reached this local server from Chromium and never from WebKit.

## Still open

- Firefox, which was not measured (Playwright's Firefox is not installed
  here).
- Safari, and the app's WKWebView on a device. The Windows WebKit shares
  WebCore's keepalive accounting but not Apple's network stack.
- A user closing a real Chrome tab: `page.close` stands for it here.
  Also, whether `visibilitychange` comes a task before `pagehide` there.
  If it does, an abort on going hidden could free Chromium's room in
  time. That is not built, since WebKit would still count the aborted
  request.
- A plain POST at a close over a real network, not to a local server.
