# Test runs and the developer's screen

The question: the developer works at the machine, over Remote Desktop, while
agents run tests, and a run that opens a window takes the screen and the
keyboard. Which local runs open one, and what keeps each out of the way?
Surveyed and measured on 5 November 2026, on Windows 11 with Electron 38.8.6.
The mechanics now in force are in
[desktop/docs/architecture.md](../../desktop/docs/architecture.md) and the rule
in [dev/debugging.md](../dev/debugging.md#local-changes-test-the-affected-modules).

## What opens a window

| Run | By default | Switch |
|---|---|---|
| `desktop/test/e2e.js` | minimized | `--headed` |
| `desktop/test/security.e2e.js` | minimized | `--headed` |
| `desktop/test/smoke.js` | minimized locally, a normal window in CI | `--headed` |
| `npm start`, `desktop/start.cmd` | a normal window on the real profile | the developer's own start |
| `frontend/tests/e2e` | headless | `--headed`: a window per worker |
| The extension's e2e, `latexEditor.mjs`, `markdownSecurity.mjs` | headless | `--headed` |
| `tools/readme-media`, `tools/branding`, `tools/user_guide` | headless | none: what they make is pixels at a fixed screen |
| An Android emulator | a window | `-no-window`, as CI starts it; `shoot-play.mjs` was run that way |
| The iPad's XCTest | Mac only, no window under `xcodebuild` | none |

`--headed` and `GAMMA_TEST_HEADED=1` are one switch, `tools/headed.cjs`, which
every suite reads and `desktop/test/headed.test.js` keeps that way.
`tools/readme-media/runtime.mjs` passes `headless: false` with `--headless=new`
to keep the extension recorder headless, so that pair stays together.

## Keeping the desktop window down

The shell's test mode (`TEST_MINIMIZED`) opened the window minimized, and four
e2e steps still brought it up with `restore()`, `show()` and `focus()`: a PDF
handed to the running app, a PDF at launch, a sign-in come back and the
background step. A focus log (the foreground window's process, polled every
50 ms, one line per change) then showed the launch itself taking the
foreground:

| Launch | `electron` held the foreground, minimized | With `setFocusable(false)` first |
|---|---|---|
| `security.e2e.js` | 1.2 s | never, in 11 runs |
| `smoke.js` | 3.0 s | never, in 3 runs |
| `e2e.js`, first start | 8.7 s, until the developer clicked elsewhere | never, in 2 full runs |
| `e2e.js`, relaunch | 3.2 s | never |
| `e2e.js`, a PDF at launch | 3.7 s | never |

One blip of about 90 ms remains, some 41 s into a full run, just after the
background step: it showed before the change and in two of the three full runs
after it. It was not traced further.

`minimize()` on a window never shown goes to `Show(kMinimized)`
(`native_window_views.cc`), which is `SW_SHOWMINIMIZED` and activates by
Microsoft's definition; the window is minimized, but it holds the keyboard.
`setFocusable(false)` before it turns activation off (`WS_EX_NOACTIVATE`, no
`CanActivate`), and on Windows it calls `SetSkipTaskbar(true)` as well, so the
taskbar button goes too. `showInactive()` is `SW_SHOWNOACTIVATE`, which also
restores a minimized window, so the background step can test the way back
without the keyboard: 35 of 35 steps pass with it.

The focus log can only see the foreground. A window restored without focus
does not show in it, which is what the suite's own check is for: on Windows a
step that leaves the window up fails.

## The alternative not taken: a parked window

The window really shown, left of every display, unfocusable and without a
taskbar button. Nothing would appear even in the background step, and the
device emulation a minimized window needs for its page size would go. The
design survived a review of the Electron and Chromium sources with no step it
would break, but it was declined for now: the brief was a minimized window, a
display change over Remote Desktop might bring it back into view, and its
remaining cases can only be settled by running them. If the background step's
moment on the screen ever matters, this is the next step.

Two things that review corrected, for whoever picks it up. `document.visibilityState`
proves nothing under Playwright: its focus emulation counts as a capturer, so
every driven page reports `visible` whatever the window does; only `--smoke`,
with no driver, shows a page's real state. And a page that was never shown is
not what stops a hidden window: the blocker is that minimizing an unshown window
activates it.

## Flakes the runs turned up

- **A hook's registry write could vanish** (`desktop/test/e2e.js`, the step
  *remote server: a URL*, about one run in six). Playwright's `evaluate` goes
  through Node's inspector, which runs a message at a V8 interrupt, in the
  middle of whatever the main process is executing. A write there landed inside
  `reconcileMirrors`' save, between its load and its write, and that save took
  it back. No app code runs that way (an IPC handler gets a turn of the event
  loop), so this was the harness's alone; `hook` now gives its body such a turn.
- **`security.e2e.js` could refuse its own port.** It asserted that `listen(0)`
  gave a port from 9100 up, and this machine's dynamic range starts at 1024
  (`netsh interface ipv4 show dynamicport tcp`). It now asks again below 9100.
- **"Stop" on a remote's folder could read the host as still kept**
  (`e2e.js`, *folder from a remote*, 4 of 17 runs on Ubuntu on 8 November
  2026). The step waited for the link to go and then read `folderHost`, which
  `dropFolder` clears only after it has revoked the token and listed the links
  left. It now waits for the bar's notice, which comes after both, and 8 runs
  since passed it.

## A false box the minimized runs brought to light

Logging the boxes showed `Could not open the origin.`, `Could not clone the
workspace.` and `Could not open the clone.` in most runs of the clone step, each
with a `loadURL` rejected as `ERR_FAILED (-2)`. They were native boxes in the
middle of a run, in the app as much as in the suite, and sometimes the rejection
failed the step. A trace of the content view's navigation events gave the
cause, and it was not the auto-login's `location.reload()`, the first guess:

```
LOADURL   ...:53310                 the server
did-finish-load, LOADURL resolved
LOADURL   ...:53310/?ws=B           the workspace, 12 ms later
did-start-navigation  ?ws=B
did-navigate-in-page  ?ws=A         the first page, booting, writes its own
did-stop-loading                    ...which ends that page's load
LOADURL rejected ERR_FAILED (-2)    loadURL takes it for the end of its own
did-navigate ?ws=B, did-finish-load and the page lands 5 ms later
```

Electron's `stopLoadingListener` makes any `did-stop-loading` that comes before
`did-finish-load` an `ERR_FAILED`. It hit 6 of 8 traced runs, headed as well as
minimized. Waiting for the first load to stop would not have done: in the PDF
step `loadURL` started with nothing loading and the old page's `replaceState`
still landed inside it.

The same overlap has a second form. When the address is written before the load
has begun, `did-navigate-in-page` resolves `loadURL` instead, 1 ms before its
`did-start-navigation`. The shell goes on, the next load cancels the one still
in flight, and Electron pins that cancellation on the new load: it is rejected
as ` (-3)`, naming the old URL, at the moment it has landed. That hit the step
after the PDF's in about one run in seven. `loadPage` also lets that rejection go
when it does come, which a load the shell does not wait for, such as a reload,
can still cause. (The auto-login's reload is no longer one: the shell now does it
through `loadPage` and waits, below.)

The first fix, `did-fail-load` as the mark of a real failure, passed 5 of 5 full
runs and a review still broke it: a load the shell's redirect guard cancels sends
no `did-fail-load`, so a server behind an http-to-https redirect or a sign-in
proxy would have sat on "Connecting" for the minute's wait, and a newer load's
landing (Home, in that minute) counted as this one's. `desktop/lib/loadPage.js`
now takes an ending for the false alarm only with an overlap to show for it, the
load before still finishing or the page's address written during the load, never
after the guard refused the load (`event.defaultPrevented` on `will-redirect`),
and aborts the wait on a newer load. `security.e2e.js` opens a server whose
redirect the guard refuses, alone and right after another load, in real
Electron; each took 60 s against an earlier version of the fix. The
suite's steps now fail on an error box, which caught the false alarm without the
fix in 2 of 4 runs.

A replay of `loadPage` through Electron's real `loadURL`, cut from the binary, ran
some 5 million runs of 66,646 event orderings, and plain `loadURL` showed 3,004
false `ERR_FAILED` where `loadPage` showed none. It also found the refused
redirect and the real failure that a stale cancellation could hold up for the
minute, both fixed. Four narrow orderings remain, accepted: each came from reading
Chromium's source, none was seen in a run, and in the first three plain `loadURL`
does the same. The old document's `did-finish-load` landing while this load is
pending settles it early, since that event names no URL; so does the old page
writing the exact address asked for; a newer load starting before this load's own
`did-start-navigation` passes for this one; and a page landing more than a minute
after a false alarm gets that alarm as its error.

### On Linux, and the auto-login's reload

Run again on Ubuntu under `xvfb-run -a` (Electron 38.8.6) on 8 November 2026,
with `loadPage` swapped back for plain `loadURL` and the same trace: 0 false
alarms in 3 full runs, 96 loads, none of them overlapped. A page there writes
its address 13 to 50 ms after its `did-finish-load`, and the next load, started
at once, has committed by then, 6 to 10 ms in. Putting 7 to 13 ms before each
load brought it back: the clone step failed on the box in 1 of 2 runs, with the
trace above line for line, and no reload anywhere near it. With `loadPage` and
the same pause, 4 runs had 5 overlapping loads and no box.

The reload was a real race all the same, the load the shell did not wait for
named above. It comes only when the window is signed out of the server it
opens, which is every switch between two local servers, since the window keeps
one `session` cookie per host (`127.0.0.1`), not per port; the clone step's
opens were all signed in. A replay in real Electron, a page that writes its
address 14 ms after it loads, the auto-login's script with its
`location.reload()`, then the next load 0 to 30 ms later, 160 times a way:
plain `loadURL` gave 5 false `ERR_FAILED` and 14 early resolutions (and 5 false
alarms without the reload, the first form), and `loadPage` 25 early
resolutions, the reload's own `did-finish-load` settling it while the page went
on to land: the first of the four accepted orderings, which the reload makes
common. Tallied by the pause, each came with the next load 2 to 18 ms after the
script answered. The page now does not reload itself: `openServerNow` loads it
again through `loadPage` and waits (`signedInAgain`), so `openServer` answers
with the signed-in page there, and that replay gave 320 of 320 clean. The step
*Gamma workspaces* opens Beta, signed out, and then its workspace at once, and
back, and checks the workspace is shown when the open resolves. Without the
change it passed 3 of 3 too: in the app the next load starts at 0 ms, where the
replay never went wrong. The headed suite passed 5 of 5 with it.

## The prerender test's `chrome is not defined`

`extension/tests/prerender.e2e.mjs` failed with `chrome is not defined`, on
`dev` as it stood before the one switch too, so headless was not the cause. It
starts Chromium itself and attaches to the extension's worker over DevTools as
soon as `Target.getTargets` lists it. On 7 November 2026, on Linux with
Playwright's Chromium 1243, the worker is listed, and `Runtime.evaluate` runs
in it with the right `self.location`, about 100 ms before Chrome installs the
extension API there: the first evaluation saw `typeof chrome` as
`"undefined"`, the next one, 125 ms later, as `"object"`. The other extension
tests reach the worker through Playwright's `serviceWorker` handle and have not
failed this way. The test now waits until `chrome.runtime.id` is there before
its first call into the worker, and passed 5 runs of 5 headless after that.

## Closed since

- The launcher's *Open data folder*, *Show server log* and *Show log file*
  called `shell.openPath` and `shell.showItemInFolder` directly, with no test
  guard. They now go through `openDirectory` and `revealFile`, which only
  record under the harness, and the steps *a server that will not start is
  explained, not dumped* and *launcher lists sizes, …* press all three.
- *Help → Check for Updates…* showed its box under the harness too. It now goes
  through `ask`, which records the box and answers with its last button, and
  the *updater* step clicks the menu item. Both passed in a full run on
  Ubuntu under `xvfb-run -a` on 7 November 2026.
