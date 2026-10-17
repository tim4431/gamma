# The iPad app as the web app, with a native pen

Survey from 2026-10-16. Findings only. What was decided and built the same
day, and how it differs from this note, is at its end
([Decided and built](#decided-and-built-2026-10-16)). The question:
the native iPad app (`ipad/`) draws its own library, notes, reader and
editing bar in SwiftUI, so it looks and behaves nothing like the web app,
and every web feature the app lacks is a gap on its list. It should
instead *be* the web app's interface, with only the PDF drawing native.
What is the architectural change? The method was a source audit of
`ipad/`, `frontend/src/ink/`, `frontend/src/markup/` and the desktop
shell, plus the platform facts below, each with its source. Nothing was
run on a device; what only a device can show is marked **device check**.
What the app does today is in [dev/ipad.md](../dev/ipad.md).

## The short version

- **Turn the app inside out.** Today Swift owns the screen and the web
  app's JavaScript decides. The change is a shell: one `WKWebView` shows
  the web app as it is, the way the desktop's Electron shell does, and
  the native code shrinks to a Pencil layer over it. The web app stays the
  one owner of the page; native is an input device and a preview layer,
  never a second editor. About 2,900 of the app's 3,600 lines of Swift
  go, and 600 to 900 come in.
- **"PDF drawing" means the pen, not the page.** The native part worth
  having is Pencil capture: the Pencil's own gestures (double tap,
  squeeze), the hover pose, and the shortest path from the tip to the
  screen for the stroke in progress. PDFKit drawing the page is declined:
  the PDF column carries most of the web app's page features, all tied to
  pdf.js's geometry, and a native column would redo or lose them, which is
  the gap the app has now.
- **The web app already has more of the pen than the research assumed.**
  Safari 18.2 added `getCoalescedEvents()` and `getPredictedEvents()`,
  which the ink layer already calls, so a WKWebView on iPadOS 18.2 or
  later gets every Pencil sample and the prediction with no change.
  Pressure, tilt and hover were already there. What native adds is the
  gestures and a frame or two of latency.
- **The web decides, native executes.** On a Pencil touch the web app's
  ink layer, which already decides whether a pointer draws, erases,
  lassoes or taps a button, tells the host "this one draws"; the host
  then takes the touch, draws the live stroke, and hands the finished
  samples back through the browser's own commit path. No rectangles, tool
  states or menus cross the bridge; the one rule of the current app, that
  Swift decides nothing, holds.
- **Offline is the fork in the road.** The web app talks to a server.
  Three ways keep offline use: a local API served from the existing
  replica, the Python backend on the device (a true clone, as the Android
  note found possible), or nothing for now. A service worker is not one
  of them: in a WKWebView it needs a static list of at most ten domains,
  which a self-hosted server's address is not on. Recommended: ship the
  shell online first, then choose between the replica and the clone with
  one measured spike each.
- **Sign-in is the web app's own page** in the web view, with its
  year-long session in the web view's cookie jar. Google refuses to sign
  in inside embedded web views, so Gamma Cloud takes the desktop's
  `app-start` / `app-claim` route through a system browser sheet; the
  server needs nothing new.

## What the app is today, and why it looks different

The app was built for two requirements the installed web app could not
meet: reading and writing with no connection, and native Pencil capture
([ipad.md](ipad.md), revisited 2026-09-29). Both were met by a native
app that runs the web app's own JavaScript (the ink codec, the notebook
rules, the editor's text commands, the mirror's merges) in JavaScriptCore,
and draws everything itself in SwiftUI: a library (`LibraryView.swift`),
a page's notes as text fields (`NotesView.swift`) with an editing bar
(`NoteEditBar.swift`), a PDFKit reader with ink overlays
(`Reader/PDFReader.swift`), a notebook view (`NotebookReader.swift`), a
tool strip (`Ink/InkToolbar.swift`), and a web view for everything else
("Open on the web", `WebPageView.swift`). The data is a replica of one
workspace in SQLite, synced by the desktop mirror's rules.

So there are three interfaces: the web app's docks, its compact shell
for an upright tablet, and the native one, which has its own look and its
own list of what is not built: highlights, text boxes drawn on the page,
the lasso, the partial eraser, folding, markdown, the `/` menu, Return in
the notes, search, chat, sharing, settings ([dev/ipad.md](../dev/ipad.md)
"Not built yet"). Every web feature arrives on the iPad twice or not at
all. That is the cost the question wants gone.

## What a WKWebView already gives the web app

A `WKWebView` is Safari's engine in the app's own process pair, so the
installed web app's behaviour ([dev/ipad.md](../dev/ipad.md) "The
installed web app") carries over, with these differences:

- **Pointer events.** `pointerType: "pen"`, pressure, tilt and hover are
  WebCore's and the same in a web view as in Safari. Since Safari 18.2
  (December 2024) WebKit also implements `getCoalescedEvents()` and
  `getPredictedEvents()` ([WebKit features in Safari 18.2](https://webkit.org/blog/16301/webkit-features-in-safari-18-2/),
  [caniuse](https://caniuse.com/mdn-api_pointerevent_getpredictedevents)).
  The ink layer calls both already (`ink/inkInput.js`,
  `ink/InkLayer.jsx` `onMove`), so on iPadOS 18.2 or later it samples the
  Pencil at its full rate and predicts the pen's next 16 ms, which
  [handwriting.md](handwriting.md) and the dev doc still described as
  Chromium-only. **Device check:** that a web view reports the same
  fields as Safari; nothing says otherwise, and no source confirms it.
- **The live canvas.** The layer's `{desynchronized: true}` canvas is a
  Chromium low-latency path; WebKit accepts the hint and has no such
  path. That frame or two is what a native live stroke saves.
- **The user agent.** A web view's `preferredContentMode` defaults to
  *recommended*: the desktop-class user agent on full-size iPads and the
  mobile one on an iPad mini ([WKWebpagePreferences](https://opensource.apple.com/source/WebKit2/WebKit2-7610.4.3.1.4/UIProcess/API/Cocoa/WKWebpagePreferences.h.auto.html)).
  The shell sets `.desktop` so every iPad looks like Safari; the layout
  follows the screen anyway (`PHONE_MQ` in `app/App.jsx`).
- **Not "standalone".** `display-mode: standalone` and
  `navigator.standalone` are false in a web view, so the web app would
  offer "Add to Home Screen" (`HOME_SCREEN_INSTALLABLE`) and skip its
  standalone stylesheet. The shell declares itself before the bundle
  runs (a `WKUserScript` at document start defining `window.gammaNative`),
  and the web app reads that in the one place it reads devices
  (`shared/lib/pointer.js`'s neighbour).
- **The status bar.** No `theme-color` painting in a web view. The shell
  paints the window behind the web view and picks the status bar style
  from the page's `data-theme`, as the desktop's preload reports it
  (`desktop/preload.js`); `viewport-fit=cover` and the safe-area insets
  work as in the installed app.
- **The keyboard.** A web view adds its own accessory bar (‹ › Done)
  above the keyboard, which Safari's app does not; the editing bar
  (`editor/EditBar.jsx`) would sit on top of it. It is hidden with the
  known `inputAccessoryView` override on the content view, which
  Capacitor and others ship (`WebShell.hideKeyboardAccessoryBar`).
  **Device check** that a device's WebKit behaves as the simulator's.
- **Memory and canvases.** WebKit's canvas limits are already the web
  app's (`shared/lib/canvasSize.js`; `pdf/pageRaster.js` releases pages
  leaving the look-ahead on WebKit). A web content process has a memory
  ceiling of its own, undocumented. **Device check** with a long PDF.
- **Media, files, downloads.** `getUserMedia` and `MediaRecorder` work in
  a web view with the microphone usage string; the recorder's MIME probe
  (`recording/recorder.js`) already takes WebKit's `audio/mp4`. An
  `<input type=file>` presents the system pickers. The web app's exports
  fetch a blob and click an anchor (`downloadFetched` in
  `shared/lib/utils.js`); a web view needs a `WKDownloadDelegate` to put
  that in Files or the share sheet. Links to other origins go to
  `SFSafariViewController`. Scribble writes in the notes' text fields as
  in any text field. **Device check** for all four.
- **Service workers** need App-Bound Domains: a static list of at most ten
  in `Info.plist` and `limitsNavigationsToAppBoundDomains`
  ([Apple forums](https://developer.apple.com/forums/thread/781744),
  [InAppWebView docs](https://www.inappwebview.dev/docs/service-worker)).
  A server whose address the user types cannot be on it. This decides the
  offline section below.
- **Debugging.** `isInspectable` (iOS 16.4) opens the page in Safari's
  Web Inspector from a Mac.

## The shape: one owner, one native surface

### The precedents

The desktop shell treats Gamma as a black box behind HTTP and a handful
of env variables, exposes nothing to the server's pages, and reads only
`data-theme` back ([dev/desktop.md](../dev/desktop.md)). The upstream
fork's iPad app is the other hybrid: the whole web app in a web view, and
a native PDFKit/PencilKit reader the web hands a page to and takes back.
Its parity matrix describes the price of two editors of one page: the web
flushes its queued edits and freezes its input before the handoff, the
native side drains an outbox and the web reloads the page on the way
back, and a pending or conflicted queue blocks the return
([WEB_PARITY.md](https://github.com/amogadget/Gamma/blob/2dbde2d/ipad/WEB_PARITY.md)).
The design here avoids that by giving the native side nothing to own: no
page, no strokes, no tool state. It takes one touch at a time and gives
it back.

### The shell

`GammaApp` shows one full-window `WKWebView` (`WebPageView.swift` is the
seed) loading the server's address, with the default website data store
so the session lasts. Before it, the address step of `ConnectView`
(the form, not the sign-in watcher). Around it: the user script that
declares the host, the message handlers, a navigation delegate for other
origins and downloads, a container view carrying the Pencil layer, and
the web content process's termination handler (reload). The shell knows
the server's address and nothing about workspaces, pages or tokens.

### The bridge

Web to native is `WKScriptMessageHandlerWithReply` in the page world:
`window.webkit.messageHandlers.gamma.postMessage({...})` returns a
promise the native side settles ([MASTG](https://mas.owasp.org/MASTG/knowledge/ios/MASVS-PLATFORM/MASTG-KNOW-0139/),
[WKScriptMessageHandlerWithReply](https://developer.apple.com/documentation/webkit/wkscriptmessagehandlerwithreply)).
Native to web is `callAsyncJavaScript` into a function the user script
defined. The whole vocabulary:

| Direction | Message | Meaning |
|---|---|---|
| script | `window.gammaNative = {platform: "ipad", version, ink: true, pencil: true}` | The host, declared before the bundle runs |
| web → native | `claim {pointerId, tool: {kind, color, size, opacity}, surface: {rect, width, height}}` | This Pencil touch is a stroke: take it |
| web → native | `theme {bg, scheme}` | What to paint behind the web view and the status bar style |
| native → web | `stroke {pointerId, t0, samples: [[x, y, p, t], …]}`, replied to after the paint | The finished stroke, in the web view's coordinates |
| native → web | `pencil {gesture: "tap" or "squeeze", action, at}` | A Pencil gesture with the system's preferred action |

Downloads and links to other origins are the navigation delegate's, not
messages. Nothing else crosses. The replica's host (`round.js`'s
interface) is a separate question (Offline, below) and would be a second
handler.

### The pen, native

The current `PencilRecognizer` (`Ink/InkPageView.swift`) already takes
Pencil-only touches with their coalesced and predicted samples beside
PDFKit's scroll views. The same recognizer moves to the web view's
container:

- **Where it sits.** A `UIGestureRecognizer` on the web view's superview
  with `allowedTouchTypes = [.pencil]`, `delaysTouchesBegan` and
  `delaysTouchesEnded` false, `cancelsTouchesInView` true, recognizing
  simultaneously with everything. A touch is delivered to the recognizers
  of the hit view and of all its superviews, so it sees every Pencil touch
  the web view gets, and keeps the samples from `touchesBegan` while it is
  still `.possible`. The web view gets the touch as usual, and the page's
  `pointerdown` reaches the ink layer's capture-phase `onDown`.
- **The claim.** Where the layer would start a stroke (its `"stroke"`
  mode: a pen or highlighter, not the eraser, lasso, move, hand or a
  tap on something), and the host has `ink`, it posts `claim` and goes on
  as in a browser. On the claim the recognizer moves to `.began`;
  `cancelsTouchesInView` sends `touchesCancelled` to the web view, which
  the page sees as `pointercancel`, and the layer's `onCancel` discards
  its draft as for any lost pointer. From then on the Pencil belongs to
  the native layer. The round trip is one `postMessage` (a few
  milliseconds); the web's draft covers that frame, so nothing is missed
  and nothing shows twice for longer than a frame.
- **No claim** (the Pencil tapping a button, scrolling in hand mode,
  erasing, lassoing, writing in the notes with Scribble): the recognizer
  fails at `touchesEnded`, having cancelled nothing. The page handled the
  touch, as in Safari. A claim that arrives after the touch ended (a dot)
  is ignored by pointer id: the page's own `pointerup` finished it.
- **Palms and scrolling.** While a stroke is captured, direct touches on
  the container are swallowed, the layer's own rule
  ([dev/handwriting.md](../dev/handwriting.md) "Palms"). Nothing moves
  the page under a captured Pencil: zoom and scroll are finger gestures,
  and the surface rectangle captured at the claim stays valid.
- **The live stroke.** A `CAShapeLayer` polyline at the tool's width and
  colour on a transparent, non-interactive view over the web view, with
  the predicted touches extending it and never stored, as the native
  app's preview was (the shell's is in [dev/ipad.md](../dev/ipad.md#the-pen),
  step 4). The outline
  through the JavaScript core would keep JavaScriptCore and the bundle for
  one function; the plain line was accepted once and is accepted again.
- **The lift.** Native posts `stroke`: `preciseLocation` in the web view's
  coordinates (CSS pixels, since the viewport is at scale 1 and the app's
  layout never scrolls the document), `force / maximumPossibleForce`, and
  the hardware timestamp in ms from the first sample, with the lift
  keeping the last pressure, as `InkPageView.swift` already does. The ink
  layer converts with the rectangle it captured at the claim (its own
  `toPt`), drops repeated points, runs `encodeStroke` with `ch: "xypt"`
  and calls `onStroke`: the browser's commit path, so the new-sheet rule,
  the replay timeline, undo, drafts and the save are untouched. It replies
  a frame after the strokes painted; native drops the polyline on the
  reply, so the stroke never blinks.

### Pencil gestures

`UIPencilInteraction` on the container gives the double tap (iOS 12.1)
and, on an Apple Pencil Pro, the squeeze with its hover pose (iPadOS
17.5) ([Apple: handling squeezes](https://developer.apple.com/documentation/applepencil/handling-squeezes-from-apple-pencil.md),
[UIPencilHoverPose](https://developer.apple.com/documentation/uikit/uipencilhoverpose.md)).
The host forwards the system's preferred action (switch to the eraser,
the previous tool, show the palette, ignore); the hover location is not
sent, since nothing in the web app places anything by it. The web app gains those as page tool actions (`markup/PageTools.jsx`
`actions`): the strip's eraser toggle, the previous preset, the ink menu
opened at a point. Safari never gets these, so this is the one thing a
native app adds that the installed web app cannot have.

### What the web app changes

- `shared/lib/host.js` (new, beside `pointer.js`): `nativeHost()` reads
  `window.gammaNative` once. The only place feature code asks.
- `ink/InkLayer.jsx`: post the claim where a stroke starts under a host;
  accept a host stroke for the surface the claim named (a registry of
  live layers by surface, or the claim's reply carrying a callback id).
  About forty lines; the sampling and commit code is reused as it is.
- `app/App.jsx`: no install offer under a host; the theme message where
  `paintStatusBar` sets `theme-color`; two tool actions for the Pencil
  gestures. `shared/styles/app.css`: the standalone rules also under a
  host attribute on `<html>`.
- Nothing else. The editing bar, the compact shell, the notebook view,
  the text boxes, highlights, replay, recordings, search, chat, sharing
  and settings are the web app's, and arrive on the iPad when they ship.

## The other reading: PDFKit draws the page

"The PDF drawing stuff" can also mean the page's pixels. Declined, for
now:

- The PDF column is where the web app's page features live, every one
  tied to pdf.js's page geometry and text layer: text selection into
  highlights, find marks, citations (`pdf/PdfCitationOverlay.jsx`), the
  translated view, note anchors, scroll alignment with the notes, text
  boxes, the lasso and the partial eraser, replay. A native column has to
  redo each or lose it; the current app's "Not built yet" list is that
  loss, and upstream's parity matrix is the same list.
- Two renderers mean two frames to keep equal (`viewportTransform`
  composed with PDFKit's page-to-overlay mapping in `PDFReader.swift`),
  work the one-renderer shape does not need.
- What PDFKit would add is raster speed: pdf.js paints on the main
  thread ([pdf-wheel-spin.md](pdf-wheel-spin.md),
  [performance.md](performance.md)). The web app has one seam for pixels,
  `pdf/pageRaster.js` (`renderRegion`), and a host could serve it from
  CoreGraphics' `CGPDFDocument` on a background queue through a
  `WKURLSchemeHandler` (`gamma-raster://<doc>/<page>?scale&rect` into an
  image), with pdf.js still parsing for text, links and the outline. The
  [zoom research](pdf-zoom-rendering.md) lists iPad and WebKit timings
  as open: measure the web app in the shell first, and take this seam
  only if the measurement says so.

## Offline: the fork in the road

The web app fetches same-origin `/api/*` and opens a page socket
(`collaboration/usePageCollab.js`). With no server reachable it does not
run. The current app's whole point was the replica; the shell has to say
what becomes of it. Four shapes:

| | What works offline | What it takes | Risk |
|---|---|---|---|
| **A. Online only** | Nothing; the web app shows its own failure | Nothing | The app loses what it has today; the replica has no consumer |
| **B. The replica behind a local API** | Reading pages, PDFs and notes; writing ink and notes; sync by the mirror's rules when back. No search, AI, sharing, settings | A `WKURLSchemeHandler` serving the web app's bundle from a copy the shell keeps (fetched from the server when online, so the offline UI is the server's own version) and answering the subset of `/api/*` the reader, the notes and the ink need from the replica's store (`Core/Store.swift`, `replica/views.js`, `replica/edits.js`). Online, the shell loads the server's origin as in A; offline, the local one. The replica's rounds run as today | The API subset is a second contract to keep (the session, the listing, `blocks/{id}/subtree`, `pages/{id}/ops`, uploads, `pdf-info`, prefs), and grows with the web app. Two origins mean two cookie jars and IndexedDB stores |
| **C. The backend on the device** | Everything the desktop clone does: the whole web app offline, the Python mirror engine as it is, no replica port | CPython for iOS (PEP 730, tier 3 since 3.13; [Python-Apple-support](https://github.com/beeware/Python-Apple-support)) on a thread, the web view at `127.0.0.1` (ATS allows local networking already), and the binary wheels for iOS: `pydantic-core`, `cryptography`, `Pillow`, `orjson`, `bcrypt`, `pypdfium2`. Mobile Forge and Flet's index have `cryptography`, `pydantic_core` and PyMuPDF ([Flet](https://flet.dev/docs/reference/binary-packages-android-ios/)); `pypdfium2` is unconfirmed | The toolchain is the project's to maintain; tens of megabytes; one wheel missing stalls it. The [Android note](android.md) found the same shape workable with Chaquopy |
| **D. A local-first web app** | The same for every client, the installed web app included | The largest: a data layer inside the web app over IndexedDB, with a service worker for the bundle | In a web view the service worker needs App-Bound Domains (above), so for the shell D degrades to B anyway |

Recommended: **A first**, which ships the interface change and stands
on its own (the desktop shell has no offline mode either, outside a
clone); then **B or C** by one spike each: B's is the API subset's size
counted against the web app's calls (29 `fetch` sites plus `apiJson`),
C's is a day building the six wheels. The replica code and its tests
(`frontend/src/replica/`, `tests/replica.test.mjs`, the `replica` browser
group, the shared fixtures that also pin the Python engine) stay until
that choice is made; the iPad bundle (`ipad/core/`, `ipad/scripts/`,
`Core/GammaCore.swift`) goes with A, since the shell runs no
JavaScriptCore.

## Sign-in and the session

- The web app's login page, in the web view. The session cookie lives a
  year in the default website data store, as in Safari; the address step
  of `ConnectView` stays as a native form (or the shell's own page), and
  the sign-in watcher (`SignInWeb`) goes.
- **Gamma Cloud.** Google refuses OAuth inside embedded web views by user
  agent; GitHub permits it. The desktop's flow transfers
  ([desktop/docs/architecture.md](../../desktop/docs/architecture.md)
  "Gamma Cloud sign-in"): post `/api/auth/cloud/app-start` with a
  loopback return and an S256 challenge, open the authorize URL in an
  `ASWebAuthenticationSession` (a system browser sheet, which Google
  allows), take the result on an `NWListener` at `127.0.0.1:<port>` and
  close the sheet by a custom-scheme redirect, then post the result and
  verifier to `/api/auth/cloud/app-claim` in the web view, which sets the
  session. The server already requires exactly a loopback return
  (`cloud_auth.app_return`), so it needs nothing new. Password sign-in
  needs none of this.
- The write token and `Keychain.swift` are the replica's; they go with
  A and come back with B.

## What changes in the repository

Swift today: 21 files, 3,635 lines. By file:

| Fate | Files | Lines |
|---|---|---|
| Goes: the interface the web app replaces | `Reader/PDFReader`, `Reader/NotebookReader`, `Ink/InkSession`, `Ink/InkToolbar`, `Views/LibraryView`, `Views/NotesView`, `Views/NoteEditBar`, `Views/PageView` | about 1,880 |
| Goes with A, returns with B | `Core/Store`, `Core/FileStore`, `Core/Replica`, `Core/Remote`, `Core/GammaCore`, `Core/JSON`, `Core/Keychain`, the bundle (`core/entry.js`, `scripts/*`, `Resources/gamma-core.js`), most of `GammaIPadTests` | about 1,080 |
| Stays, reshaped | `App/GammaApp`, `App/AppModel` (the address; no sync cadence), `Views/ConnectView` (the form), `Views/WebPageView` (becomes the main view), `PencilRecognizer` out of `Ink/InkPageView` | about 450 |
| New | the bridge (user script, handlers with reply, `callAsyncJavaScript`), the capture layer (recognizer, live polyline, finger swallow), the gestures, the navigation and download delegates, the cloud sign-in | 600 to 900 |

The app ends near 1,000 to 1,300 lines, none of which knows a block, a
page or a token. On the web side: `host.js`, about forty lines in
`InkLayer.jsx`, about thirty in `App.jsx`, a stylesheet rule.

Tests: the web half is testable without a Mac. A Playwright step in the
`ipad` browser group defines `window.gammaNative` with `addInitScript`,
presses with a pen, asserts the claim, posts a `stroke` and checks the
saved ink (`frontend/tests/e2e/scenarios/ipad.mjs`); a unit test covers
the sample conversion. The Swift half is a recognizer state machine over
synthesized touches, which XCTest does poorly; it gets the device check
list below, as the editing bar has today. `ipad.yml` keeps generating and
building the project; its path filters lose the replica, notebook and
editor paths with the bundle, and `check.yml`'s core step goes with it.
`backend/tests/test_ipad_contract.py` pins answers only the replica
reads, so it goes with A too.

Docs: [dev/ipad.md](../dev/ipad.md)'s native-app half becomes the shell
and the pen; "The installed web app" stays as it is, since the shell is
that app with a Pencil layer.

## Device checks and risks

1. `cancelsTouchesInView` from the container's recognizer reaching the
   page as `pointercancel`, with no scroll begun meanwhile. Everything
   rests on this; try it first. The current app's recognizer coexists
   with PDFKit's scroll views the same way, which is the reason to expect
   it to work.
2. The claim's round trip under load: a `postMessage` from the page's
   `pointerdown`, typically a few milliseconds. A stroke that ends before
   the claim is the page's. The page's pointer id means nothing natively,
   so the shell matches a claim to the Pencil touch that began where the
   claim says the pointer went down, and drops stale ones
   ([dev/ipad.md](../dev/ipad.md#the-pen), step 3).
3. Pointer events in a web view equal to Safari's: pressure, tilt, hover,
   and 18.2's coalesced and predicted events.
4. The keyboard accessory bar under the editing bar, and the visual
   viewport's keyboard behaviour the bar depends on
   ([dev/ipad.md](../dev/ipad.md) "Where it sits").
5. The memory ceiling with a book-length PDF, and the main-thread paint
   on a 120 Hz screen: the measurement that decides the raster seam.
6. `MediaRecorder`, the download delegate for exports, the file picker,
   the clipboard.
7. The Gamma Cloud sheet: Google in `ASWebAuthenticationSession`, the
   loopback return on iPadOS, the sheet closed by the custom scheme.
8. The handoff: the native polyline against the web's outline. A plain
   line at the tool's width was accepted in the current app; a user who
   sees the swap will say so.
9. App Review guideline 4.2 (minimum functionality) refuses apps that are
   a web site in a web view; the Pencil layer, the gestures, offline use
   and the share sheet make this one more than that. The app is built
   from source today, so this waits for a store decision.
10. A terminated web content process (`webViewWebContentProcessDidTerminate`)
    drops the page; the shell reloads and the web app's session
    restoration does the rest. An unsaved ink draft is lost as it would be
    in a killed Safari tab ([touch.md](touch.md) lists that gap).

## Decisions left to take

1. **Offline:** A then B, A then C, or straight to C. The replica's and
   the bundle's fate follow from it.
2. **The live stroke's look:** the plain line (recommended) or the outline
   through the core, which keeps JavaScriptCore for one function.
3. **The raster seam:** only after measuring pdf.js in the shell on a
   device.
4. **The offline bundle's source** (B only): a copy fetched from the
   server, so it matches the server's API, or one built into the app.

### Decided and built (2026-10-16)

Offline went A then C, and both were built the same day: the shell first,
then the backend on the device, run as the desktop runs its sidecar.
Offline copies are the mirror's clones, made in the web app. The replica
and the JavaScriptCore bundle were removed, since neither phase uses
them. The live stroke is the plain line (decision 2), the raster seam
waits for a device (3), and decision 4 went with B. The mechanics are in
[dev/ipad.md](../dev/ipad.md). What differs from this note:

- The claim carries where the pointer went down, and the shell matches it
  to the Pencil touch that began there, since the page's pointer id means
  nothing natively. It carries no surface: the web keeps the rect and zoom
  of the down itself. The stroke carries no `t0` either: the web stamps it
  with its own clock at the down.
- A `stroke` with no samples means the system cancelled the touch: the
  claim ends and nothing is saved.
- The web drops its draft on the `{taken: true}` reply rather than on a
  `pointercancel`, which WebKit may not send. It then ignores that
  pointer's events and treats fingers as palms until the stroke ends.
- The server picker is declared by the host (`servers`) and sits on the
  pages shown before the library (the sign-in page, an unavailable
  workspace, a blocked share link) as well as in the account menu.
- `pypdfium2` has no iOS build, so the server on the device reads PDF text
  through PyPDF2 and renders no page images. Background running is not
  built: the server stops while iOS suspends the app.

## Sources

- WebKit, [features in Safari 18.2](https://webkit.org/blog/16301/webkit-features-in-safari-18-2/): `getCoalescedEvents()` and `getPredictedEvents()`; [caniuse](https://caniuse.com/mdn-api_pointerevent_getpredictedevents).
- Apple, [WKWebpagePreferences](https://opensource.apple.com/source/WebKit2/WebKit2-7610.4.3.1.4/UIProcess/API/Cocoa/WKWebpagePreferences.h.auto.html) (content modes), [WKScriptMessageHandlerWithReply](https://developer.apple.com/documentation/webkit/wkscriptmessagehandlerwithreply), [OWASP MASTG on content worlds](https://mas.owasp.org/MASTG/knowledge/ios/MASVS-PLATFORM/MASTG-KNOW-0139/).
- Service workers and App-Bound Domains: [Apple forums 781744](https://developer.apple.com/forums/thread/781744), [forums 773151](https://developer.apple.com/forums/thread/773151), [InAppWebView](https://www.inappwebview.dev/docs/service-worker).
- Apple Pencil: [handling squeezes](https://developer.apple.com/documentation/applepencil/handling-squeezes-from-apple-pencil.md), [UIPencilHoverPose](https://developer.apple.com/documentation/uikit/uipencilhoverpose.md), [coalesced touches](https://developer.apple.com/documentation/uikit/getting-high-fidelity-input-with-coalesced-touches.md), [predicted touches](https://developer.apple.com/documentation/uikit/minimizing-latency-with-predicted-touches.md).
- Python on iOS: [PEP 730](https://peps.python.org/pep-0730/), [Python-Apple-support](https://github.com/beeware/Python-Apple-support), [Flet's binary packages](https://flet.dev/docs/reference/binary-packages-android-ios/), [pydantic wheels issue](https://github.com/pydantic/pydantic/issues/12739).
- Upstream's hybrid: [amogadget/Gamma WEB_PARITY.md](https://github.com/amogadget/Gamma/blob/2dbde2d/ipad/WEB_PARITY.md).
