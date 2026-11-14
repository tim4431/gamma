# Gamma for Android

The Android app (`android/`, [android/README.md](../../android/README.md))
is the iPad app's design on Android. It is a shell over Gamma's own web
app: one WebView shows a Gamma server's page under the desktop app's own
bar and launcher, and the app adds what Chrome cannot give that page.
That is a stylus stroke captured natively, with its line drawn ahead of
what the page could paint, the stylus's buttons as pen gestures, a Gamma
Cloud sign-in that works with Google, files handed over from other apps
and saved into Downloads, and a Gamma server of its own inside the app
(**This device**), so it works with no connection.

It answers the iPad app's host contract and shows the same chrome pages.
This file describes what is Android's own, and links
[ipad.md](ipad.md) for what the two apps share:
[the host contract](ipad.md#the-host-contract), the chrome's
[state](ipad.md#the-state) and its rules for
[opening, failing and starting](ipad.md#opening-failing-and-starting),
the pen's protocol ([The pen](ipad.md#the-pen)), what the page does with
[opened files](ipad.md#opened-files), and the server entry and its wheels
([The local server](ipad.md#the-local-server)). Why the app has this
shape, what else was weighed and what Google Play asks of it:
[research/android.md](../research/android.md).

| | |
|---|---|
| Package | `com.gammapdf.gamma`, the application id and the Kotlin namespace: gammapdf.com reversed, permanent once the app is created in the Play Console. The iPad's bundle id, `net.blitzbuild.gamma`, belongs to another Apple team; nothing ties the two |
| Android | 10 (API 29) and later, targeting API 36; phones and tablets, in any orientation |
| Distribution | an APK on every GitHub release, and Google Play ([Release](#release), [Publishing on Google Play](#publishing-on-google-play)) |

Handwriting is the same `gamma-ink` file whichever wrote it
([handwriting.md](handwriting.md)): the app's live line is a preview, and
the web's ink layer encodes and saves every stroke.

## What the user does

1. **Pick a server.** The first launch shows the launcher, the desktop
   app's page of servers: **This device**, the server inside the app, or
   another server added by its address (a NAS, a hosted Gamma). This
   device's card names its data folder "App storage › Gamma › local", and
   the launcher's footer reads "Gamma for Android <version>". A server
   may be plain `http`: the app allows cleartext to any address, where
   the iPad allows it on the local network only. A server signed by an
   authority the person installed in Android's settings loads too
   ([The address](#the-address)).
2. **Sign in.** On This device the app signs in by itself as `admin`
   ([The account and the silent sign-in](#the-account-and-the-silent-sign-in)).
   On another server the person signs in on its own login page, with a
   password, or with Gamma Cloud through the system browser
   ([Gamma Cloud sign-in](#gamma-cloud-sign-in)).
3. **From then on everything is the web app.** A stylus writes through
   the native capture ([The pen](#the-pen)). The line under the tip has
   the preset's look and follows the pressure and the tilt the stylus
   reports; draw and hold snaps a shape, and a stroke by the ruler's edge
   follows the edge. A finger scrolls and pinches as in Chrome, and while
   a stroke is captured, other touches on the screen (a palm) do
   nothing. A stroke begun with the stylus's first button held erases. A
   click of the first button with the pen hovering does what the iPad's
   double tap does (the eraser, by default), and a click of the second
   button what its squeeze does (the last tool), unless Settings →
   Reading & editing → **Stylus** (*Pen button*, *Second pen button*,
   *Haptics*) says otherwise.
4. **The bar** above every page is the desktop app's
   ([The chrome](#the-chrome)). With a keyboard, Ctrl+Shift+L is the
   launcher, Ctrl+R reloads, and Ctrl+Shift+B hides or shows the bar; the
   system's list of keyboard shortcuts (Meta+/) names them. The system's
   Back closes the bar's menu, then the launcher, then goes back in the
   page's history on the server shown, and otherwise puts the app in the
   background as it is.
5. **Offline**, a workspace of another server is cloned onto This device
   in the web app's settings ([Offline use: clones](#offline-use-clones)).
6. **Files from other apps.** Files' *Open with* and the share sheet hand
   Gamma a PDF, a Markdown note, a `.zip` or an image; the share sheet
   also hands it Notability's and GoodNotes' exports. Each opens in the
   web app as the same file picked there would ([Opened files](#opened-files)).
7. **Files saved.** A downloaded PDF, an export, the lasso's SVG or a
   file chip's file goes to Download/Gamma, and a banner at the bottom
   says "Saved “name” to Downloads", with *Open* ([Saving files](#saving-files)).
8. **Recording and dictation** ask for the microphone the first time
   ([Recording](#recording)).

The Kotlin side holds no page, stroke, tool or token. It keeps the local
server's seeded password, sealed in the keystore, the servers listed and
their names, the bar's choice, the theme's colour and name, and the local
server's port. The session is the WebView's cookie (CookieManager, written
to disk when the app goes to the background), and the library is the
server's, This device's included.

## How it is put together

The web app is the one owner of the page. Native code is a window, an
input device, a preview layer and, for This device, a process manager,
and nothing in Kotlin knows what a block is. So a web feature reaches the
app when the server has it (This device's at the app's next build), with
no Android work.

- **One window.** `MainActivity`, a `ComponentActivity`, holds the shell
  (`ShellController`) edge to edge. What must outlive an activity belongs
  to the process (`GammaApplication`): the model the shell follows
  (`AppModel`, the iPad's ported), the local server, whose interpreter
  starts once per process, the files opened in the app until the page
  takes them, and the files being saved. An activity made again finds
  them as they were.
- **Threads.** The shell, the pen and the model are main-thread
  confined. Blocking work (HTTP, files, sockets, the keystore, Python)
  runs on `Dispatchers.IO`, launched from the process's main-thread
  scope, and nothing blocks the main thread.
- **One CSS pixel is one dp.** The page's WebView has `textZoom = 100`,
  and the page's viewport is `width=device-width, initial-scale=1`, so a
  CSS pixel is a density-independent pixel, and the pen divides a
  MotionEvent's pixels by the display's density. Everything on the bridge
  is in viewport CSS px, as on the iPad.
- **Pure where it can be.** Whatever can be decided without a device
  lives in files with no `android.*` import (JSON goes through
  `json/Json.kt`), which the JVM tests run ([Tests](#tests)). The Android
  files around them are thin: views, intents, MotionEvents, sockets.

Under `android/app/src/main/` (the Kotlin under
`kotlin/com/gammapdf/gamma/`):

| File | What it does |
|---|---|
| `app/GammaApplication.kt` | The process: its main-thread scope, the model, the local server, the opened files (`cacheDir/Opened`) and the saves (`cacheDir/Saves`). Back in front (`ProcessLifecycleOwner`'s start) it has the local server checked; going to the background it writes the cookies to disk. A debug build opens its WebViews to Chrome's inspector |
| `app/MainActivity.kt` | The window, edge to edge; the services that register for activity results, made in `onCreate`; the intents that open files, which `OpenedEntryActivity` hands it; a second window that a tap on the icon made, closed at once; Back; the keys, a stylus button's first, then the shell's shortcuts |
| `app/AppModel.kt` | The iPad's `AppModel`: the server shown, the remotes and their names, opening, a failed load or start sent to the launcher, whether the launcher and the bar are up, the remotes' health |
| `app/ShellState.kt` | The state the bar and the launcher read ([The chrome](#the-chrome)) |
| `app/SavedServers.kt`, `app/ServerAddress.kt` | The servers kept between launches (`gamma.servers`, one JSON value) and what the address field accepts (a bare host means `https`, a link keeps only its origin, a host goes to ASCII) |
| `app/LocalHost.kt`, `app/Prefs.kt`, `app/AndroidPrefs.kt` | The local server as the model sees it, and the key-value store over the SharedPreferences file `gamma` |
| `shell/ShellController.kt` | The window's views and insets, the bar's placing, the theme, Back and the shortcuts; it answers the page's messages and the chrome's calls, and carries the pen's strokes to the page ([The shell](#the-shell)) |
| `shell/PageView.kt` | The page's WebView, the iPad's `WebShell`: its settings, opening a server, reloading, the theme's paint, the session read, the silent sign-in, and a new WebView after its renderer ends |
| `shell/Navigation.kt`, `shell/Origin.kt`, `shell/PageLoad.kt` | Where a navigation goes, new windows, the page's dialogs, a failed load in the launcher's words, and the file chooser, the microphone and downloads handed to their services |
| `shell/Session.kt`, `shell/SessionWire.kt` | The session read and the silent sign-in, made natively with the WebView's cookie |
| `shell/ShellKeys.kt` | The shortcuts, and what Back does |
| `shell/Bridge.kt`, `shell/HostBridge.kt`, `shell/HostWire.kt`, `assets/shell/host.js` | The host contract ([The bridge](#the-bridge)) |
| `shell/ChromeViews.kt`, `shell/ChromeCalls.kt`, `shell/ShellFailure.kt`, `assets/shell/chrome.js` | The bar and the launcher, their calls and the failures they show ([The chrome](#the-chrome)) |
| `shell/CloudSignIn.kt`, `shell/CloudFlow.kt`, `shell/CloudWire.kt`, `shell/LoopbackReturn.kt`, `shell/CloudReturnActivity.kt` | [Gamma Cloud sign-in](#gamma-cloud-sign-in) |
| `shell/OpenedEntryActivity.kt`, `shell/OpenedIntake.kt`, `shell/OpenedFiles.kt` | [Opened files](#opened-files) |
| `shell/FileChooser.kt`, `shell/FileTypes.kt` | [Picking files](#picking-files) |
| `shell/Saves.kt`, `shell/SavePresenter.kt`, `shell/SaveNames.kt`, `shell/Fetch.kt` | [Saving files](#saving-files) |
| `shell/Microphone.kt` | [Recording](#recording) |
| `pen/PenLayer.kt`, `pen/LiveInk.kt`, `pen/PenGestures.kt` | The pen on the device: MotionEvents in and out, the live line, the buttons ([The pen](#the-pen)) |
| `pen/PenRouter.kt`, `pen/PenCapture.kt`, `pen/LiveLine.kt`, `pen/PenButtons.kt`, `pen/Tilt.kt`, `pen/PenHaptics.kt` | The pen's pure parts written for Android: what the page gets of each event, the capture, the live line's geometry frame by frame, the button clicks, the pose, the haptics |
| `pen/ClaimQueue.kt`, `StrokeRecorder.kt`, `HoldWatch.kt`, `HeldShape.kt`, `HoldReply.kt`, `InkTool.kt`, `InkWidth.kt`, `LazyBrush.kt`, `Ribbon.kt`, `RulerSnap.kt`, `CssColor.kt` | The iPad's Pencil logic ported line for line, its rounding included, held to the same shared fixtures |
| `server/LocalServer.kt`, `server/DeviceServer.kt` | This device's server: the web app unpacked, the port, the data directory, the account, the environment, the wait for `/api/health`, the restart ([The local server](#the-local-server)) |
| `server/PythonRuntime.kt` | Chaquopy's CPython, started once on a thread of its own, running `gamma_mobile.run` |
| `server/WebAssets.kt`, `LocalEnvironment.kt`, `LocalPorts.kt`, `LocalLogin.kt`, `KeystoreVault.kt`, `Health.kt`, `ServerState.kt` | Its parts: unpacking the web app, the environment, the port rule, the password and its keystore seal, the health rule, the supervisor's state file |
| `json/Json.kt` | JSON in and out, over `org.json` |
| `AndroidManifest.xml`, `res/` | The three activities (the window, the way in for opened files, the sign-in's way back), the permissions, the intent filters and the FileProvider; the network security config, the backup rules, the strings, the theme, and the launcher icon, a brand output ([design/brand/README.md](../../design/brand/README.md)) |

`backend/gamma_mobile.py` and `backend/requirements-mobile.txt` are the
server's entry and pins, shared with the iPad ([The local server](#the-local-server)).

## The shell

`ShellController` is the controller of the iPad's `ShellView.swift`,
ported; `PageView` is its `WebShell.swift`.

- **The window.** The root view is painted in the page's theme colour.
  Inside it, a frame sits within the system bars, the display cutout and
  the keyboard (the window's insets, applied as the frame's margins), so
  the strips under the status bar and the navigation bar, and beside a
  cutout, show the root's colour. The insets are consumed there: the
  WebViews see none, `env(safe-area-inset-*)` is zero in every page, and
  the pages pad for nothing. With the keyboard up the frame ends above
  it (`adjustResize`), so the page resizes rather than pans, which the
  pen's places rely on. Bottom to top the frame holds the pen layer (the
  page's WebView with the live line over it), the launcher's WebView
  while it is up, and the bar's WebView, a 44 dp strip at the top
  (`bar.html`'s touch row) that is see-through around its row.
- **The bar's placing.** The page and the launcher start under the
  strip, or at the frame's top while the bar is away. The bar hides with
  its chevron, Ctrl+Shift+B or the web app's View → *Server bar*, all
  through `AppModel.setBarHidden` (kept as `gamma.barHidden`), and it
  still shows while it is needed (the launcher is up, or This device
  starts), as on the iPad ([The chrome](ipad.md#the-chrome)). It fades
  over 200 ms while the content's top moves, at once when the system's
  animations are off (the iPad's Reduce Motion), and a faded bar is
  `GONE`, so the page takes every touch. When the person's choice
  changes, the page hears `shellBar`, and a hide the shell did itself has
  the page's guide say once where the row is
  ([onboarding.md](onboarding.md), the server bar hint).
- **The theme.** The page's `theme` message paints the window and the
  WebView's background in its colour, and the colour and the theme's
  name are kept (`gamma.theme.background`, `gamma.theme.name`), so the
  app opens in the colour it closed in and the chrome paints in that
  theme. The icons of the status and navigation bars follow the page's
  scheme, or the colour's lightness before any page has reported one.
  The activity itself stays on the system's light or dark (the
  `DayNight` theme), so a page on the "system" theme still reads the
  system's choice; a window no page has painted yet follows the system
  when it changes.
- **The page's WebView.** JavaScript and DOM storage are on.
  `textZoom = 100`, because the web app has its own scale (Settings →
  Appearance) and the system's font size would apply on top of it. There
  is no zoom, no file or content URL, no third-party cookie, no
  algorithmic darkening (the web app has dark themes of its own) and no
  overscroll. Media may play without a gesture, since a recording's
  player starts after an await. Multiple windows are on, so
  `target=_blank` reaches the shell (below).
- **Navigation** (`Navigation`, `Route`), by the desktop's and the iPad's
  rules. The server's own pages stay in the WebView. Another site in the
  main frame opens in a Custom Tab, and an embed inside the page stays.
  `mailto:` and other apps' links go to the system, and a toast says so
  when no app takes one. `target=_blank` and `window.open` get a second
  WebView that is never shown, only to learn the address, which then
  leaves the app the same way, a same-origin one too; a window that never
  navigates is let go after 10 s. The page's `alert` and `confirm` are
  dialogs. The Gamma Cloud start goes to `CloudSignIn`, the file chooser
  to `FileChooser`, the microphone to `Microphone`, and a download the
  page starts by itself to `SavePresenter`.
- **A failed load.** A main-frame failure goes to the launcher
  (`AppModel.loadFailed`) in the sentence the iPad's URL loading system
  gives for the same failure, with Chromium's code after it (`PageLoad`):
  "Could not connect to the server. (net::ERR_CONNECTION_REFUSED)". A
  load given up because another took its place is no failure. A
  certificate the WebView does not trust is refused, and on the server's
  own page that is the failure "An SSL error has occurred and a secure
  connection to the server cannot be made." An HTTP error page is still a
  page. A page that commits clears the failure, except the WebView's own
  error page, which commits under the address that failed.
- **The session, read natively** (`Session`). The bar's switcher needs
  `/api/session`, and the silent sign-in needs `/api/login`. The iPad
  runs both in a script world the page cannot reach; a WebView has no
  such world, so the shell asks them itself over `HttpURLConnection`,
  with the WebView's own cookie for the server, and the Set-Cookie of a
  sign-in goes into CookieManager. The page never sees the password. The
  read follows the iPad's rules ([The state](ipad.md#the-state)): after
  every finished main-frame load, on every change of the page's address
  (`doUpdateVisitedHistory`: the web app writes `?ws=` with
  `replaceState`), and on the chrome's `state` call; only while the page
  is on the shown server's origin and not loading, and one read at a
  time.
- **A renderer that ends.** When the system ends the WebView's renderer
  (memory, mostly), that WebView cannot be used again. A new one takes
  its place under the pen layer, the bridge is attached to it, and it
  loads the address the old one was on, or the server's address when
  that was elsewhere; the web app restores itself as after any reload,
  and the silent sign-in is due again. A chrome page's WebView is
  replaced the same way, the bar back to its strip. The app's process
  lives on.
- **The activity's life.** The manifest keeps the activity through
  rotation, a resize, a keyboard, the navigation mode and the system
  going light or dark (`configChanges`), so the WebViews and their
  documents stay. It is `singleTop`, so a tap on the app's icon only
  brings the app's task forward and leaves what is open above the window
  (the Gamma Cloud sign-in's tab, a picker), and a file opened from another
  app comes through `OpenedEntryActivity` ([Opened files](#opened-files)).
  The system still starts a second window for that tap when something is
  open above the first and the task was last brought up by another intent
  (an opened file, the sign-in's return); not being the task's first, it
  closes at once. A change the activity does not take itself (the
  language, the font size, the density) makes it again: the process's
  model and server are as they were, and the new shell loads the shown
  server's address. In the background the WebViews are paused (the page
  sees `visibilitychange`); in front they resume.
- **Back** (`BackStep`), the first that applies: an open bar menu closes;
  the launcher closes when it is up over a chosen server and shows no
  failure; the page goes back in its history; otherwise the app goes to
  the background as it is (`moveTaskToBack`). A launcher that shows a
  failure, or has no server under it, is the app's error page, and Back
  leaves the app rather than walk the page under it back unseen. The
  page goes back only to an entry on the shown server's origin: one
  WebView shows every server, and its own back passes neither `Route`
  nor the bridge, which is set up for the shown server alone. So
  `PageView.open` clears the history once the new server's first page
  has committed (`doUpdateVisitedHistory`), and Back checks the entry
  before the current one as well.
- **The keys** (`Shortcut`). `MainActivity.dispatchKeyEvent` sees a key
  before any WebView: a stylus button's key first ([The pen](#the-pen)),
  then Ctrl+Shift+L, Ctrl+R and Ctrl+Shift+B, with exactly those
  modifiers (Ctrl+Shift+R is no reload), each acting on its first down
  and consumed. The system's shortcut list names them, the bar's as
  "Hide server bar" or "Show server bar".

## The bridge

The host contract ([ipad.md "The host contract"](ipad.md#the-host-contract))
over androidx.webkit (`HostBridge`). Two message ports carry strings
between the pages and the shell, one JSON object per message
(`HostWire`):

| Port | Between | The pages' side |
|---|---|---|
| `gammaAndroidHost` | the server's page and the shell | `assets/shell/host.js` |
| `gammaShellHost` | the chrome pages and the shell ([The chrome](#the-chrome)) | `assets/shell/chrome.js` |

A port pairs nothing, so a page's call carries an id,
`{id, …}`, and its answer names it, `{reply: id, value}` or
`{reply: id, error: <sentence>}`. A page's first message, `{hello: true}`,
gives the shell that document's reply proxy, which is how the shell
reaches the document.

- **Declared per server.** `PageView.open` attaches the bridge to the
  page's WebView for the server's origin before its first load: the port
  (`WebViewCompat.addWebMessageListener`) and `host.js` as a
  document-start script (`addDocumentStartJavaScript`), with its
  placeholder `__GAMMA_HOST_INFO__` replaced by the app's `HostInfo` as
  JSON, keys sorted. Opening another server removes both and adds them
  again for its origin. An origin the WebView's rules cannot spell is
  given `*`, and the shell checks each message's origin itself. A WebView
  without both features has no bridge, and the page runs as it would in
  Chrome.
- **What it declares** (`HostInfo` in `Bridge.kt`): `platform:
  "android"`, `version` (the app's versionName), `ink`, all seven
  `inkLooks`, `hold`, `pencil`, `pencilGestures: ["tap", "squeeze"]`,
  `haptics` (true when the device has a vibrator), `openFiles`, `save`
  and `shellBar`; no `pencilPhases` and no `pencilRoll`, since a stylus's
  buttons have no squeeze phases and Android reports no barrel roll.
  The app's script, in the top frame only, defines `window.gammaNative`
  with those flags, its `post(msg)`, which the web's `postToHost`
  (`frontend/src/shared/lib/host.js`) prefers to WebKit's handler, and
  the `receive` stub that queues until the web's module takes it over. It
  sets `data-host="android"` and says hello.
- **Page to shell.** `{id, msg}` is taken only from the main frame of
  the server's origin; anything else gets "Only the Gamma server's page
  talks to the app.", and a message that does not decode "Gamma for
  Android does not understand this message." A message that is not a
  string (an `ArrayBuffer`) is ignored on both ports, since reading it as
  one throws. `ShellController` answers
  each, off the main thread where it does IO: a `claim` by the pen layer,
  `theme` by the window, `haptic` by `performHapticFeedback` on the
  page's WebView ([The pen](#the-pen)), `files`, `fileData` and
  `fileDone` by the opened files, `shellBar` by the model, and the four
  save messages by the saves ([Saving files](#saving-files)).
- **Shell to page.** `{send: n, msg}` goes to the document that said
  hello. Its script hands `msg` to `window.gammaNative.receive` and
  answers `{result: n, value}` (`null` when the page resolved with
  nothing, which the shell reads as `{}`) or `{result: n, error}`. `send`
  waits for that answer, `ask` (which `hold` uses) also hands back the
  value, and `post` does not wait. A message waits only on its own
  document (`PendingSends`): when another document says hello, when a
  main-frame load fails into the WebView's error page, or when the bridge
  is detached, what still waits fails rather than waits, and the saves
  the old document was sending are dropped (`onDocumentGone`). The WebView may
  report `onPageStarted` before the new document commits, so a new
  document is known by its hello, and nothing fails at `onPageStarted`; a
  navigation that turns out to be a download does not drop the bridge.

`frontend/tests/host.test.mjs` and the `android` browser group run
`host.js` as shipped over a stand-in port ([Tests](#tests)).

## The chrome

The bar and the launcher are the desktop app's own pages,
`desktop/ui/bar.html` and `launcher.html`, as on the iPad
([ipad.md "The chrome"](ipad.md#the-chrome)); the build copies
`desktop/ui/` into the app's assets under `chrome/`
([Building](#building)). A change to them is a change to all three shells.

- **Served from the app.** `WebViewAssetLoader` serves the assets at
  `https://appassets.androidplatform.net/assets/chrome/`, an https origin
  nothing else uses (the WebViews load no `file:` URL). Each page has a
  WebView of its own (`ChromeViews`): the bar's is see-through and never
  scrolls, the launcher's starts in the theme's saved colour until it
  paints. Only the bundled pages load in them; a link out of one leaves
  the app (a Custom Tab, or the system).
- **`gammaShell`.** `chrome.js`, at document start for that origin only,
  defines what the desktop's preload and the iPad's `Chrome.swift`
  define: `platform: "android"`, `onState(cb)`, and one function per
  method below, each a promise over the port `gammaShellHost`. A call is
  answered only for the main frame of a page under `/assets/chrome/` on
  that origin (`ChromePages.accepts`); anything else gets "Only Gamma's
  own pages talk to the app." The state is pushed as `{state}` to every
  page that has said hello, when the model, the local server, the page's
  session or its theme changes; a page that loads later asks for it.
- **What the pages show.** `bar.html` and `launcher.html` name the shell
  from `gammaShell.platform` (their `SHELLS` table): "this device" and
  "Gamma for Android", and the keys Ctrl+Shift+L, Ctrl+R and
  Ctrl+Shift+B. Both set `body[data-touch]` for the touch sizes and to
  hide the desktop's `.desktopOnly` controls (*New local server*, and the
  local card's Remove). The Android bar has no safe-area padding: the
  shell places it below the status bar and paints that strip itself.
- **The bar** is the 44 dp strip of [The shell](#the-shell). While a
  dropdown is open (`barExpand(true)`) its view covers the whole content,
  so the menu has room and a tap beside it closes it. The launcher's
  toggle, Ctrl+Shift+L and Back first close an open dropdown through the
  bar's page (an Escape keydown, which `bar.html` closes it on) and put
  the view back to its strip.
- **The launcher** is made when it shows, over the page and under the
  bar, and destroyed when it closes; each showing is a fresh load
  (`AppModel.Launcher`), with the failure it shows as `?error=`, the
  desktop's JSON percent-encoded (`ShellFailure`).
- **The state** is the iPad's (`ShellState.make`; its keys and rules in
  [ipad.md "The state"](ipad.md#the-state)), with This device's card in
  Android's words: `dataDir` "App storage › Gamma › local", and with no
  password kept, "This device keeps no password for this account. A
  password the server made itself is in its log." While This device
  starts, the bar says "Starting This device…", and after a minute "Still
  starting This device… the first start after an install or an update can
  take a few minutes". A failed start shows "Gamma could not start on
  this device" with the log's last 4 KB.

The calls are the iPad's ([ipad.md "The bridge"](ipad.md#the-bridge)),
answered by `ChromeCalls`, with these differences:

| Method | On Android |
|---|---|
| `revealData` | The app's page in the system's Settings, which shows its storage: the data folder is in the app's private storage, which no file manager shows |
| `revealLog` | The server's log, `server.log`, handed to the share sheet as text through the app's FileProvider (`${applicationId}.files`, `res/xml/file_paths.xml`); a toast when there is no log yet |
| `copyAddress` | This device's address to the clipboard while it runs, resolving with it; nothing otherwise |
| `setUrl`, `remove` | Refused for This device: "This device's address is its own.", "This device cannot be removed." |
| any other | "Gamma for Android has no <method>."; a call whose arguments do not read, "Gamma for Android cannot read this call to <method>." |

## The pen

The protocol is the iPad's ([ipad.md "The pen"](ipad.md#the-pen)): the
web decides, native executes. The page's ink layer decides what a touch
is, claims a pen or highlighter stroke with its place and the preset's
look, gives way on `{taken: true}`, answers `hold`, and commits the
samples the shell hands back at the lift through its own path. What
follows is how the Android shell does its part, generally for any
stylus that reports `TOOL_TYPE_STYLUS` (an S Pen, a USI pen, Wacom EMR).
With nothing armed on a PDF page the page claims nothing and selects text
or draws the rectangle ([handwriting.md](handwriting.md) "Claiming
input"): the stylus reaches the WebView as it came, and what stops the
pan and the palm is the page's `onTouch`.

1. **Where.** `PenLayer` is a `FrameLayout` holding the page's WebView
   and the live line's views. Its `dispatchTouchEvent` sees every touch
   the page gets, so the bar and the launcher, outside it, never reach
   the pen. The page sits at the layer's origin, so a sample is a
   MotionEvent's position over the density, in the page's CSS px.
2. **Recording.** Every stylus pointer the page is given is recorded
   from its down, under a token of its own (`PenRouter`: a pointer id
   comes back for a later touch, a token never does), with the event's
   history first. The pressure is read where the stylus's device has a
   pressure axis with a range; otherwise the samples say 0.5. The pose is
   read where it has a tilt axis: the altitude is π/2 minus `AXIS_TILT`,
   and the azimuth is `AXIS_ORIENTATION` plus π/2 (`Tilt`; the samples
   carry degrees, as the iPad's do). Times come from `eventTimeNanos`
   (API 34 and later) or `eventTime`. A sample is `[x, y, p, t]` or
   `[x, y, p, t, a, z]`, `t` in ms from the stroke's first sample; there
   is no roll.
3. **Every sample as it is measured.** From a stylus's `ACTION_DOWN`, or
   at the next move when the stylus joins a gesture later, the layer asks
   for unbuffered dispatch (`requestUnbufferedDispatch`), so the system
   delivers each sample when the digitizer measures it instead of once a
   frame.
4. **The page gets every event as it came** until a claim, the WebView
   deciding as in Chrome. The one exception: a stylus that touches down
   with its first button held (`BUTTON_STYLUS_PRIMARY`) goes to the page
   as the eraser (`TOOL_TYPE_ERASER`) for the rest of its stroke, so the
   page erases with it as it does with the barrel in a browser. That
   stroke is not recorded and is never claimed. A pen's own eraser end
   reaches the page as it is.
5. **The claim** (`PenCapture.claim`, `ClaimQueue`) takes the oldest
   recorded stroke that went down within 10 CSS px of the claim's place,
   if it is still down and no other is captured; an ended stroke is
   remembered for half a second, so a late claim cannot take the next
   one at the same spot. Taken, the page's stream ends with one
   `ACTION_CANCEL` for the pointers it had (with `FLAG_CANCELED` on API
   33 and later), and the rest of the gesture is swallowed, fingers too:
   that is the palm, and the page cannot move under the pen. Once the
   captured stylus is up, a stylus that touches down is the page's again,
   in a stream of its own that starts with an `ACTION_DOWN`, and can be
   claimed in turn. Events rebuilt for the page keep their place on
   screen.
6. **A cancel is no lift.** A lift that carries `FLAG_CANCELED` (API 33
   and later: the system's palm rejection, or an accidental touch), and
   an `ACTION_CANCEL`, cancel the captured stroke: its line goes, and the
   page hears `stroke` with no samples. On API 29 to 32 every lift is a
   lift.
7. **One device per gesture.** A gesture is one input device's, from its
   `ACTION_DOWN` to its `ACTION_UP` or `ACTION_CANCEL`; another device's
   events in between are dropped, and another device's `ACTION_DOWN`
   ends the gesture under way (a capture is abandoned), as Android ends
   one device's gesture for the next's.
8. **The live line** (`LiveLine`, `LiveInk`). Its geometry is the iPad's
   `LiveStrokeView.show`: the held shape's outline, the ruler's line, the
   ribbon by the brush's width law, a dashed line or the plain polyline,
   in the claim's look. Here a frame is split in two: the `settled` part,
   which only grows (the lazy brush only adds points, and every point but
   the last has its final width, worked out once as the point comes, with
   a Pencil's opacities kept as a running sum: `LiveWidths`), and a
   `tail` made again each frame, the last point and the prediction;
   together they are exactly the iPad's line, which a JVM test checks
   frame by frame, the widths and the shade against the whole stroke's.
   `LiveInk` draws the line being written into the front buffer of a
   transparent `SurfaceView` above the page (graphics-core's
   `CanvasFrontBufferedRenderer`), which the display shows while it is
   drawn into. Each frame clears and draws again only the region the line
   changed in, and the whole buffer at the stroke's first frame, when its
   colour changes (a held shape's 34 ms flash in the selection blue, a
   Pencil's shade) and when the line turns into an outline or back. The
   renderer keeps every frame it is given until the stroke ends, and a
   polyline frame's path is the whole line so far, so a frame lets its
   path go once drawn. At the lift the line goes to the renderer's
   multi-buffered layer by a commit, which hides the front buffer in the
   same transaction, so the hand-off never blinks, and it stays there
   until it is released. A highlighter multiplied onto the page is drawn
   by a plain View under the `SurfaceView` with `BlendMode.MULTIPLY`,
   since a `SurfaceView`'s layers never read the page's pixels; that View
   draws every line when the renderer could not be made, or the surface
   is not ready when a stroke starts.
9. **Prediction.** A `MotionEventPredictor` runs over the gesture, and
   the captured stroke's line runs ahead by its points, capped as the
   web's `predictedInkSamples` caps them (16 ms and 12 CSS px), when the
   tool neither smooths nor follows the ruler and no shape has been
   answered. A shape stops the prediction for the rest of the stroke (the
   iPad resumes it when the shape is dropped).
10. **The hold.** The page sees nothing of a captured stroke before its
    lift, so the shell keeps the timer: `HoldWatch`, 600 ms after the
    stillness anchor's sample, which moves when a sample lands more than
    3 CSS px from it, posted on the main looper (`Handler.postAtTime`).
    It sends `hold` with exactly the samples the ask counted, and the
    page's answer comes back through `PenCapture.held`: a shape is drawn
    at once and refitted as the pen moves (`ShapeHold`, held to
    `tests/shared/inkhold.json`); a cancel takes the line away and keeps
    the touch captured, so the palm stays swallowed and the lift sends
    no samples; nothing lets the next still spell ask again. An answer
    after the lift changes nothing.
11. **The lift.** The line is drawn once more from exactly the samples
    handed back, without the prediction, and the page hears `stroke` with
    them and `held`, the hold whose shape the line was drawn as. The line
    stays until the page's promise resolves, after it has painted the
    stroke, or for three seconds.

**The buttons** (`PenButtons`, `PenGestures`). A click is a stylus
button pressed and released while the pen hovers, with no contact in
between. The first button (`BUTTON_STYLUS_PRIMARY`) is sent as `pencil`
with `gesture: "tap"` and `action: "switchEraser"`, the second
(`BUTTON_STYLUS_SECONDARY`) as `gesture: "squeeze"` and `action:
"switchPrevious"`, without a phase; the web then applies the person's
choice for each ([settings.md](settings.md)). A press during which the
pen touches the screen, leaves its range (`ACTION_HOVER_EXIT`) or is
cancelled is no click, and no new press counts until the button has been
seen up while the pen hovers. The first button held as the pen touches
down reaches the page as an eraser (`PenRouter`'s rewrite, which also
keeps Chromium's own stylus text selection off that stroke), the second
held then goes on as an ordinary stroke, and either pressed later does
nothing, since Chromium reports a contact's buttons as 1. The
layer feeds every stylus
MotionEvent to `PenButtons`, from the touch stream and from the generic
one (hover, and `ACTION_BUTTON_PRESS` and `RELEASE`), and passes each on
to the page unchanged. Some styluses also report their buttons as keys
(`KEYCODE_STYLUS_BUTTON_PRIMARY` and `SECONDARY`, API 34), which the shell
takes before any view by the same rules; a key click within 0.3 s of the
motion click of the same button is that click, counted once. The tail
button (`KEYCODE_STYLUS_BUTTON_TAIL`) stays the system's, which opens a
notes app. Losing the window's focus forgets a press. On the emulator,
keys injected with `input keyevent` never reached the app (the system
appears to keep them), so the hover button events are the path that
matters; a real pen has not been tried.

**Haptics** (`PenHaptics`). The page's `haptic` plays as a
`HapticFeedbackConstants` value through `performHapticFeedback` on the
page's WebView, under the system's touch-feedback setting. The motor is
in the device, not the pen, so the message's `at` changes nothing here.

| Kind | API 34 and later | Earlier |
|---|---|---|
| `snap` | `GESTURE_THRESHOLD_ACTIVATE` | `CONFIRM` (API 30 to 33), `CONTEXT_CLICK` (API 29) |
| `select` | `SEGMENT_TICK` | `CLOCK_TICK` |
| `tool`, and a kind the shell does not know | `TOGGLE_ON` | `KEYBOARD_TAP` |

## Gamma Cloud sign-in

Google refuses to sign in inside an embedded WebView, so the page's own
start is taken over, as the desktop and the iPad take it over
([ipad.md "Gamma Cloud"](ipad.md#gamma-cloud), `desktop/lib/cloudSignIn.js`),
and the server needs nothing new. The contract is `CloudWire`, the flow
`CloudSignIn`.

1. The page navigates its main frame to `<server>/api/auth/cloud/start`
   (the login page's button, Settings' Link), which `Route` hands to
   `CloudSignIn` instead of loading it.
2. A listener opens on `127.0.0.1` at a free port (`LoopbackReturn`).
   Only this device reaches that address (RFC 8252 §7.3), so a sign-in
   someone else started never hands its result to them.
3. The shell posts `app-start` to the server itself, with the WebView's
   cookie for it, so a Link carries the session it links to: the start
   URL's `next`, `link` and `invite`, the listener's
   `http://127.0.0.1:<port>/cloud-signin` as `return_to`, and the S256
   challenge of a verifier only the shell keeps. A server too old for the
   route gets "This server is older than the app. Update it to sign in
   with Gamma Cloud here, or sign in with a password."
4. The authorize URL opens in the system browser, where Google's sign-in
   works and the browser's own Google or GitHub session can be used: an
   Auth Tab when the default browser has one, else a Custom Tab. With no
   browser at all the person is told "Gamma Cloud sign-in needs a web
   browser, and this device has none."
5. The account server sends the browser to the listener with a one-time
   result (or an error). The listener reads the request's first line,
   keeps the first answer, then redirects the browser to
   `gamma-android://cloud-signin`. It reads the rest of the request's
   head first, since a socket closed with bytes unread sends a reset and
   the browser may lose the redirect. Any other request gets a 404.
6. The shell sends `app-claim` as soon as the listener has the result,
   not when the tab closes: a form post of the result and the verifier,
   with the `X-Gamma-Desktop: claim` header and the WebView's cookie, its
   redirect not followed. The answer's Set-Cookie goes into CookieManager,
   which is written to disk, and the WebView loads where the redirect
   leads on the server (to `next` signed in, or to the login page with
   `?cloud_error=`); a redirect off the server lands on its home page.
7. The browser comes back to the app. An Auth Tab hands the redirect to
   the app itself; after a Custom Tab, `gamma-android://cloud-signin`
   opens `CloudReturnActivity`, which shows nothing, hands the return to
   the running sign-in and brings `MainActivity` forward, closing the tab
   above it. Either only ends the wait.

`app-start` and `app-claim` are sent natively (`CloudFlow`), where the
iPad sends them from inside its web view: a WebView has no script world
the page cannot reach, and a WebView's own POST cannot carry
`app-claim`'s header. One sign-in runs at a time. A start while one asks
the server or claims is a second tap and does nothing; a start while the
browser is open replaces that sign-in, since a Custom Tab says nothing
when the person closes it. The listener stops when its sign-in ends, when
another replaces it, when the shell shows another server, after ten
minutes, and when the activity ends. A browser outside the app's task (a
Custom Tab moved into the browser, or no Custom Tabs browser at all)
leaves the bar usable while the sign-in waits, so its landing is loaded
only while the server it began on is still the one shown. An
error reaches the person as a dialog: "Gamma Cloud sign-in did not
finish: …" for the account server's error, "Gamma Cloud sign-in: …" for
anything else. A password sign-in is the web app's own login page.

A known limit, not fixed: as on the desktop
([cloud_accounts.md](cloud_accounts.md#the-gamma-side-sign-in-with-gamma-cloud)),
other apps on the device can reach the loopback listener while a sign-in
waits, so a hostile app that finds the port can answer first and spoil
that sign-in. It cannot sign in as the person: the PKCE verifier never
leaves the app. A per-sign-in secret in the return address would close
it, but the server's `app_return` check (`backend/gamma/cloud_auth.py`)
refuses any query there and would have to accept one first.

## Opened files

The manifest's intent filters, on `OpenedEntryActivity`, offer Gamma for
the types the web app opens (`AndroidManifest.xml`):

- **Files' *Open with*** (`ACTION_VIEW`, `content:` URIs): PDF, Markdown
  (`text/markdown`, `text/x-markdown`), `.zip`, and the images a page
  takes as a picture (JPEG, PNG, GIF, WebP, HEIC, HEIF, SVG).
- **The share sheet** (`ACTION_SEND`, `ACTION_SEND_MULTIPLE`): the same
  types, and `application/octet-stream`, which is how Notability's
  `.note` and `.ntb` and GoodNotes' `.goodnotes` come, since no type of
  their own is registered. *Open with* does not offer Gamma for those.

`OpenedEntryActivity` shows nothing. It hands a copy of the intent to the
window in the app's own task (`FLAG_ACTIVITY_NEW_TASK`, `CLEAR_TOP`,
`SINGLE_TOP`) and closes: the file comes to the window already open
(`onNewIntent`), closing what was above it, or to a new window in a task
of the app's own, never to one in the sending app's task. Of the sender's
flags only the grant to read goes with the copy, so the window holds the
grant from then on, after the entry has closed; the sender's launch flags
(a new document, no history) are not the window's. Reopened from Recents,
the entry passes on no files.

`OpenedIntake` takes the intent's files, from `onCreate` and from
`onNewIntent`. An activity made again, or one reopened from Recents, does
not take its intent's files a second time. It copies each file into
`OpenedFiles` on an IO thread, under the
name its provider gives (`OpenableColumns.DISPLAY_NAME`, else the URI's
last segment), with the type of its name's extension (Markdown added by
hand), else the provider's, else none. The original is only read. It
refuses a folder, a file it cannot read, a `content:` URI served by one
of the app's own providers, and a `file:` URI inside the app's own data,
since another app could name the app's private files that way; a toast
says "Gamma could not open “name”." (or how many could not be opened).

`OpenedFiles` keeps each copy in a folder of its own under
`cacheDir/Opened` and tells the page (`filesOpened`). From there the web
side is the iPad's ([ipad.md "Opened files"](ipad.md#opened-files)): the
page asks for the list (`files`) once it is signed in to a workspace,
reads each file in 2 MiB base64 chunks (`fileData`), says `fileDone`, and
only then does the copy go. So a file opened before a server is chosen,
on the sign-in page or while This device starts waits until a page can
take it. The folder is emptied when the process starts, so a file the
page had not taken yet when the system ended the app is gone, for
instance one handed over during a long first start; the iPad has the
same limit.

## Picking files

A WebView shows no file picker by itself: the page's
`<input type="file">` reaches `onShowFileChooser`, and `FileChooser`
answers it.

- An input that takes pictures only (an accept of any image, the
  strip's Picture button) gets the system's Photo Picker, one picture or
  several. On Android 10 and 11 without the system Photo Picker, androidx
  falls back to the document picker.
- Anything else gets the document picker (`ACTION_OPEN_DOCUMENT`),
  offering the MIME types of the input's `accept` (`FileTypes.accepted`).
  An accept with a token no table knows (`.goodnotes`, `.edn`), or none,
  offers every file: a narrower picker would hide a file the page takes,
  and the server checks what it is given anyway.
- `multiple` picks several. A folder input (`webkitdirectory`) is picked
  as several files, without their relative paths, since Android's
  pickers hand back no folder a page can read; the web app offers no
  folder picker under Android (`canPickFolder` in `transfers/folderZip.js`).
- Only `content:` URIs go back to the page: a file path from a picker
  could name the app's own files. The WebView's callback is always
  answered, with nothing when nothing was picked, since one left
  unanswered keeps the page from opening a picker again; a picker the
  page asked for before and never heard back from is answered with
  nothing first.

A save-mode request is answered with nothing: the web app saves through
the bridge ([Saving files](#saving-files)).

## Saving files

A WebView's download listener can neither read a `blob:` URL nor learn
an `<a download>` name, so the app declares `save`, and the web app
(`shared/lib/saveFile.js`) hands it the files it saves through the
bridge: `save`, `saveData` and `saveDone` for bytes the page holds, and
`saveUrl` for an address the shell fetches itself. The messages and
their replies are in [ipad.md "The host contract"](ipad.md#the-host-contract).

- **Bytes from the page** (`Saves`). `save` begins a file of exactly
  `size` bytes in a folder of its own under `cacheDir/Saves` and replies
  with its id, or with `{}` when the size is negative or more than the
  disk has free. `saveData` appends the next chunk (the page sends
  2,097,150 bytes at a time, each base64 on its own), and an unknown id,
  an offset that is not the bytes so far or a chunk past the size is an
  error reply ("Gamma for Android is saving no such file, or these bytes
  are out of turn."). `saveDone` replies `{saved}` at once, true only for
  a file of the size it was begun with, and the file is presented after
  the reply; with `cancel` what there was goes. A save is its document's:
  when that document goes (the bridge's `onDocumentGone`: a reload,
  another page, a failed load, a new renderer or another server), the
  saves it had not finished are dropped at once and their files deleted
  off the main thread. The folder is emptied when the process starts.
- **An address** (`saveUrl`). The reply is `{}` at once, and the shell
  fetches the address on an IO thread (`Fetch.get`): an `http` or
  `https` address, as a browser downloads a link (another scheme is
  refused, "Gamma for Android saves only http and https addresses."),
  since a note's picture may come from another site. The page that asks
  may be any server's, though, and the shell's request is held to none of
  the browser's rules (CORS, SameSite), so it keeps rules of its own
  (`SaveHops`), checked on every hop (a redirect is followed hop by hop,
  at most five):
  - the WebView's cookies go only to the shown server's origin, so a
    remote server's page never sends the local server's session, and
    another site gets no cookie at all;
  - an address on this device that is not the shown server is not asked
    at all ("Gamma for Android fetches nothing on this device but the
    server shown."): its host is looked up as the connection would look it
    up, and a loopback or unspecified address is refused, so a remote
    page cannot reach the local server's port;
  - an address other parsers could read differently (a user name, an
    escape, a backslash or a non-ASCII letter in its host) is refused,
    "Gamma for Android does not fetch an address written this way.", since
    the cookie store and the connection each read the address themselves.

  The saves the web app makes stay as they were: its backups, job
  artifacts, recordings and uploads are on the server, with its cookies,
  and a picture from another site or a redirect to storage needs none.
  Nothing is retried: a job's artifact counts as downloaded once it is
  fetched.
- **A download the page started itself**, not through the bridge (an
  attachment it navigated to), reaches the WebView's `DownloadListener`
  and is fetched the same way, by the same rules for the server shown; a
  `blob:` or `data:` one lives in the page, beyond the shell's reach, and
  a toast says "This file could not be saved."
- **The name** (`SaveNames.choose`): the page's name for the file when
  it gave one, since an upload is served under its hash and a job's
  artifact under the job's; then the response's Content-Disposition
  (`filename*`, then `filename`); then the last segment of the address;
  then "Download".
- **Where** (`SavePresenter`). The file goes into Downloads, in a Gamma
  folder (`Download/Gamma/`), through MediaStore, which needs no
  permission. It is hidden from other apps (`IS_PENDING`) until it is all
  there, and a name already taken gets MediaStore's own " (1)". The MIME
  type is the one of the name's extension, or none for an extension no
  table knows (so the name stays as it is), or, for a name without an
  extension, the type it came with, whose extension MediaStore then adds.
- **The banner.** A bar at the bottom of the window says "Saved “name”
  to Downloads", with the name MediaStore gave it, and *Open*, which
  offers the apps that open it, Gamma left out (it would import the file
  again). It stays four seconds, or what Android's accessibility settings
  ask for, and a newer one takes its place. A failure is a toast, "Gamma
  could not save “name”: …". The iPad hands a download to its share
  sheet instead; a file saved with no tap at all (an export that
  finished) would make a sheet pop up unasked, so here it is put away and
  the banner says where.

## Recording

Recordings and dictation use the page's `getUserMedia`. The manifest
declares `RECORD_AUDIO` and `MODIFY_AUDIO_SETTINGS`, with the microphone
as a feature the app can do without (`android:required="false"`), since
Google Play would otherwise take the permission to require one and leave
out a device that has none. `Microphone`
answers the WebView's permission requests: the microphone
(`RESOURCE_AUDIO_CAPTURE`) is granted to the server's own origin once the
person has let the app record, the system asking the first time;
anything else, another origin, the camera, or the page after a no, is
denied. Requests that come while the system's question is open wait for
its answer, and one the page withdraws is dropped. The iPad grants the
microphone to the server's origin the same way. Where the audio goes is
the web app's ([recording.md](recording.md)).

## The local server

"This device" is Gamma's own backend running inside the app on
`127.0.0.1:<port>`, as on the iPad
([ipad.md "The local server"](ipad.md#the-local-server)) and as the
desktop app runs a sidecar. The shell shows it like any other server, so
the whole web app works with no connection, and the Kotlin side stays a
process manager (`LocalServer`): it unpacks the web app, picks the data
directory and the port, seeds the first account, starts the interpreter
and says when the server answers. Nothing in it knows what the server
holds.

### What runs

- **CPython 3.13 from Chaquopy** (17.2.0), packaged by the Chaquopy
  Gradle plugin ([Building](#building)). `PythonRuntime` starts it once
  per process (`Python.start`) on a thread of its own, `gamma-server`,
  with a 16 MiB stack, as the iPad's has, since a thread's default stack
  is too small for the server's imports. The interpreter is never
  finalized and cannot be started twice in one process.
- **The backend from `backend/`, unchanged**: `app.py`, the `gamma`
  package and `gamma_mobile.py`, the entry both apps share, with the
  built web app from `frontend/dist`. `gamma_mobile.run(env)` takes the
  environment as JSON (the iPad sets its process environment before the
  interpreter starts and passes none), makes it the process's, sends
  Python's output to the log, imports the server and runs uvicorn on
  `127.0.0.1` at `GAMMA_PORT` under the supervisor the iPad's description
  covers ([ipad.md "What runs"](ipad.md#what-runs)). It never returns
  while the supervisor runs. `PythonRuntime.restart` calls
  `gamma_mobile.restart()` from another thread, which takes the GIL for
  the call; it can do nothing when the interpreter never came up or the
  entry itself ended (an import error), which only a new build fixes.
  What fails on the Java side before Python's output is redirected is
  appended to the log, marked with its time. The server starts no process
  here, having no Python to run in one: an SVG picture, which another
  server draws in a process it kills after 10 s, is drawn in the server
  with no time limit, as on the iPad (`sys.platform == "android"`,
  [pictures.md](pictures.md#svg-pictures)).
- **What `gamma_mobile.py` does on Android only.** Threads the server
  starts get an 8 MiB stack (`threading.stack_size`), glibc's default,
  which the server has on Linux: Android's threads get bionic's 1 MiB,
  and the request handlers' worker threads run deep validation and PDF
  code.
  The shared libraries Flet's wheels keep in an `opt/lib` folder of
  their own (Pillow's libjpeg, libtiff, libwebp, libfreetype and
  libc++_shared) are loaded by their path before anything imports the
  extensions that need them (`_load_wheel_libraries`), because Android's
  linker never looks beside an extension; a library that fails is tried
  again after the others until a pass loads nothing more. An import
  finder (`UntaggedExtensions`) loads PyMuPDF's two extensions, which
  Flet names `_extra.cpython-313.so` and `_mupdf.cpython-313.so` without
  the platform tag, so neither Python's Android suffixes nor Chaquopy's
  importer matched them; `libmupdf.so` beside them is found through their
  own `$ORIGIN`. The process's descriptors 1 and 2 are not pointed at the
  log, as the iPad points them: on Android they are the whole app's, and
  an emulator's graphics driver writes a line to them for every shader
  the WebView compiles. Python's `sys.stdout` and `sys.stderr` are the
  log, and `faulthandler` writes the threads' stacks there before a
  crash in native code.

### The wheels

The same pins as the iPad's, `backend/requirements-mobile.txt`: Chaquopy's
pip installs them at build time as Android wheels, for each ABI the build
packages, from PyPI and [Flet's index](https://pypi.flet.dev), which
publishes the Android builds of the native packages PyPI lacks. What the
pins change against the server's, and why, is in
[ipad.md "The wheels"](ipad.md#the-wheels). `tools/check_mobile_pins.sh`
resolves every pin for Android's `arm64-v8a` and `x86_64` as well as the
iPad's two platforms, installing nothing (`check.yml`'s `mobile-pins`).
With PyMuPDF in the app, This device reads PDFs as every other server
does, its page pictures included; the device test proves it
([Tests](#tests)).

The server reads files beside its modules (the onboarding content, icons,
certifi's bundle, the math fonts), so every package is extracted to real
directories (`extractPackages("*")`). Chaquopy does that, with the
standard library, at the first start after an install or an update: that
is the slow first start the bar tells about.

### Where the data lives

- **The library**, `GAMMA_DATA_DIR`, is `files/Gamma/local` in the app's
  internal storage, which no other app reads and on whose filesystem
  SQLite's write-ahead log is safe. No file manager shows it, so the
  launcher's card names it "App storage › Gamma › local", and *Open data
  folder* opens the app's page in the system's Settings. Clearing the
  app's storage, or uninstalling it, deletes it.
- **The web app** the server serves (`GAMMA_STATIC_DIR`) is
  `files/frontend_dist`, unpacked from the app's assets (`WebAssets`):
  the build puts `frontend/dist` there with a list of its files and a
  stamp of their contents, and a stamp that differs from the one kept
  beside the folder (`files/frontend_dist.stamp`) unpacks it again, into
  a folder of its own that is then renamed into place, so an interrupted
  copy is never served. It happens before the interpreter starts:
  `gamma.app` registers the page routes only when the folder exists at
  import. One lock for the process runs the unpackings one at a time, so
  two servers starting together (the device test's beside the app's)
  never write into the folder at once. A build without the web app fails with "This build of Gamma has
  no web app in it (frontend/dist was not among its assets)."
- **Temporary files** (`TMPDIR`) go to `cache/gamma-tmp`, which is
  emptied before the interpreter's first start in a process. Android
  gives an app no temporary folder of its own, and Python's `tempfile`
  would otherwise have none.
- **Backups and a move to a new device** leave out the library, the
  unpacked web app, the Python and packages Chaquopy unpacks
  (`files/chaquopy/`, with its record of them, the `chaquopy` prefs) and
  the sealed password (`res/xml/data_extraction_rules.xml`, and
  `backup_rules.xml` before Android 12): the servers listed and their
  names, the bar's choice and the theme come along. The unpacked files
  are made again at the first start, and Chaquopy's alone are many times
  the 25 MB a cloud backup may hold, past which the app is not backed up
  at all. A cloud backup also leaves out the WebView's folder
  (`app_webview/`: the servers' session cookies and the pages' storage),
  so no session goes to the cloud; a move to a new device carries it.

### The address

The server keeps its port by the desktop's rule, as the iPad does
([ipad.md "The address"](ipad.md#the-address)): the port it had last
time (`gamma.local.port`) while that is free, else a free one in the IANA
dynamic range from a random point, else any the system hands out
(`LocalPorts`). A kept port keeps the origin, and with it what the web
app stores per origin. The address is `http://127.0.0.1:<port>`; the
network security config allows cleartext, which the WebView, the health
probes and the shell's own requests all need here.

The same config trusts the certificates the person installed in Android's
settings beside the system's (`<trust-anchors>` with `system` and `user`):
a NAS signed by an authority of its own loads, which an app targeting API
24 or later otherwise refuses ([https.md](https.md)). A `<domain-config>`
cannot name the person's servers when the app is built, so the trust is
the whole app's: the Gamma Cloud sign-in in the WebView trusts a
user-installed or a company's authority too, as a browser on the device
would. The embedded Python keeps certifi's bundle, which matters only for
the device's own server reaching another (a clone's rounds). A
self-signed certificate trusted by its fingerprint, as the desktop does,
is not built here (the WebView's `onReceivedSslError` would carry it).

### The account and the silent sign-in

A first start, on a data directory with no `users.db`, seeds one
account: `admin`, with a random 12-character password (`LocalLogin`, the
desktop's shape). The password is sealed with AES-GCM under a key that
lives in AndroidKeyStore and never leaves it, and the sealed text is kept
in the SharedPreferences file `gamma.local-login` (`KeystoreVault`), where
the iPad keeps it in the Keychain. The key is in no backup and no
transfer, so a restored file opens to nothing, the iPad's case of a
backup restored without its Keychain. The app passes the account as
`GAMMA_ADMIN_USER` and `GAMMA_ADMIN_PASSWORD` on that start only. The
launcher's *Show credentials* shows it. A data directory whose password
the vault does not hold gets no silent sign-in: the person signs in on
the page, with a password the server made itself, which is in its log.

The shell signs in to This device as the desktop does, natively
([The shell](#the-shell)): after the first finished main-frame load on the
local server's origin, it asks `/api/session` with the WebView's cookie,
and when that is anonymous it posts `/api/login` with the seeded account,
puts the cookie the answer sets into CookieManager, and reloads. It runs
only on the local server's origin, and once per open: This device opened
from the launcher or the bar, *Try again*, a reload that loads the
server's address again, or a new WebView after its renderer ended. A
person who signs out stays signed out until This device is opened again.

### The environment

`LocalEnvironment`, passed to `gamma_mobile.run` as JSON:

| Variable | Value |
|---|---|
| `GAMMA_DATA_DIR` | `<files>/Gamma/local` |
| `GAMMA_STATIC_DIR` | `<files>/frontend_dist` |
| `GAMMA_VERSION` | the app's versionName, shown as the server's build |
| `GAMMA_CLOUD_DEFAULT_ISSUER` | `https://account.gammapdf.com`: Gamma Cloud sign-in is on until the admin picks another account server or none ([cloud_accounts.md](cloud_accounts.md)) |
| `GAMMA_PORT` | the port above |
| `TMPDIR` | `<cache>/gamma-tmp` |
| `GAMMA_ADMIN_USER`, `GAMMA_ADMIN_PASSWORD` | the seeded account, on a first start only |

`gamma_mobile.py` adds the mobile apps' defaults under them:
`GAMMA_UPDATES=app` and `GAMMA_UPDATE_CHECK=off` (the app brings the
server's updates, so the admin dashboard asks GitHub nothing and says the
server is part of the app), and `CRYPTOGRAPHY_OPENSSL_NO_LEGACY=1`.

### Starting, and the log

The server starts at launch when This device was the last server opened,
and otherwise the first time it is opened. It then runs while the app's
process does, whichever server the window shows.

- **Before the interpreter** (`LocalServer.prepare`, off the main
  thread): the data folder, the last launch's state file removed, the web
  app unpacked, the temporary folder, the port and the account. A failure
  here (no free port, no data folder, no web app) is tried from the
  beginning by *Try again*.
- **While it starts**, the bar says so, and the app asks `/api/health`
  every 250 ms, by the desktop's rule (a 2xx, or the 503 with
  `data_directory_not_upgradable`). There is no deadline. After a minute
  the start is marked slow and the polling drops to once a second. On a
  warm emulator the server answers in about seven seconds.
- **A failed start** is what the supervisor reports in
  `<data>/logs/server.state`, read between the probes (`ServerState`), or
  the interpreter's own end. While This device is the server chosen, the
  launcher shows the detail and the log's tail with *Try again*, which
  calls `LocalServer.restart`: the server again in the same interpreter,
  on the same port.
- **Back in front.** Android may stop or starve a process in the
  background. When the app comes to the front again (the process's
  lifecycle start), a running server is asked `/api/health` up to three
  times, a second apart, and one that stays silent is run again in the
  same interpreter, on the same port, so the page's origin and what the
  web app keeps per origin stay.

Everything the server prints goes to `<data>/logs/server.log`, appended
across launches, each start marked `--- <UTC time> start port <port> ---`.
A failed start shows its message and the log's last 4 KB in the launcher,
and *Show log file* hands the whole file to the share sheet. How to read
it from a computer is in [android/README.md](../../android/README.md#logs).

### Offline use: clones

This device is a Gamma server like the desktop's local one, so the
offline copies of another server's workspaces are Gamma's own clones,
made in the web app as on the iPad
([ipad.md "Offline use: clones"](ipad.md#offline-use-clones)). A clone
syncs only while the local server runs: while the app's process lives
and This device has been opened in it.

## Building

The commands are in [android/README.md](../../android/README.md).

### The toolchain

- **JDK 21**, which Gradle runs on (the code compiles to Java 17
  bytecode); CI uses Temurin 21.
- **The Android SDK** with platform 36 and the build tools, found
  through `ANDROID_HOME` or `sdk.dir` in `android/local.properties`.
- **Python 3.13.** Chaquopy runs pip under the app's Python version, so
  environment markers resolve as on the device. It looks for `py -3.13`,
  `python3.13`, `python3` and `python` itself; `-Pgamma.buildPython`
  names another, by its path (spaces and all) or as a command line.
- **Node.js** for the web app, unless `frontend/dist` is built already
  and `-Pgamma.webBuild=prebuilt` is passed.
- **The network**, the first time: Gradle's dependencies, and the wheels
  from PyPI and Flet's index. Both are cached afterwards.

The versions are in `android/gradle/libs.versions.toml`: the Android
Gradle plugin 9.5.0, which compiles Kotlin itself (there is no Kotlin
plugin), the Gradle 9.8.2 wrapper, Chaquopy 17.2.0, and the androidx
libraries (WebKit, browser, graphics-core, input motion prediction,
lifecycle-process) with kotlinx coroutines.

### The project

`android/` is a Gradle project (`gamma-android`) with one module, `:app`.
It finds the repository as its parent directory and takes from it the
server (`backend/`), the pins, the built web app (`frontend/dist`) and the
chrome pages (`desktop/ui/`). A build:

1. **`buildWebApp`** runs `node tools/build_frontend_if_needed.mjs` from
   the repository's root (with `npm ci --prefix frontend` first when
   `frontend/node_modules` is missing), which rebuilds `frontend/dist`
   only when `frontend/` changed. `npm run build` empties that folder
   while it runs, so a development server serving it meanwhile shows
   errors. Under `-Pgamma.webBuild=prebuilt` the step is skipped and
   `frontend/dist` only has to exist.
2. **`bundleWebAssets`** puts `desktop/ui/` under the assets' `chrome/`,
   and `frontend/dist` without its Brotli and gzip copies (the server
   sends the plain file when there is no copy, and nothing is gained by
   compressing over loopback) under `frontend_dist/`, with
   `frontend_dist.files`, the list of its files, and `frontend_dist.stamp`,
   a SHA-256 over their paths and contents.
3. **Chaquopy** packages Python 3.13 for each ABI, pip-installs
   `backend/requirements-mobile.txt` with Flet's index as an extra index,
   and takes from `backend/` only `gamma/**`, `app.py` and
   `gamma_mobile.py`, without `__pycache__`.

Nothing is minified. The JVM tests get the repository's path as the
system property `gamma.repo`, for the shared fixtures. The outputs are
under `android/app/build/outputs/` (`apk/debug/`, `apk/release/`,
`bundle/release/`). `android/.gitignore` keeps out `.gradle/`, `.kotlin/`,
`build/`, `local.properties` and Android Studio's files.

### Properties

Passed with `-P` on the command line, or set in `gradle.properties`:

| Property | Default | What it does |
|---|---|---|
| `versionName` | `<desktop/package.json version>-dev` | The app's version, `GAMMA_VERSION` for the server and the launcher's footer. A release passes its own |
| `versionCode` | `1` | Android's version number; a release passes major × 1,000,000 + minor × 1,000 + patch ([github_actions.md "Versions and tags"](github_actions.md#versions-and-tags)) |
| `gamma.abis` | `arm64-v8a,x86_64` | The ABIs packaged, comma-separated. Chaquopy's Python packages are assets, which no ABI split divides, so each ABI adds its own; a release passes `arm64-v8a`, the emulator test `x86_64` |
| `gamma.webBuild` | `auto` | `prebuilt` skips the web build and only requires `frontend/dist` |
| `gamma.buildPython` | Chaquopy's search | The Python 3.13 that runs pip: the path of an interpreter, taken whole, or else a command line split on spaces (`py -3.13`) |

### Signing

A release build is signed when the environment names a keystore that
exists, and is unsigned otherwise, so a fork builds:

| Variable | Value |
|---|---|
| `GAMMA_ANDROID_KEYSTORE` | the keystore file's path |
| `GAMMA_ANDROID_KEYSTORE_PASSWORD` | its password |
| `GAMMA_ANDROID_KEY_ALIAS` | the key's alias |
| `GAMMA_ANDROID_KEY_PASSWORD` | the key's password; for the PKCS12 keystore `keytool` makes, the keystore's |

CI decodes the `ANDROID_KEYSTORE_BASE64` secret into the runner's temporary
folder and points `GAMMA_ANDROID_KEYSTORE` at it, in the one step that
builds ([github_actions.md "Google Play, once"](github_actions.md#google-play-once)
makes the key). The same key is the app signing key Google Play signs its
installs with ([Publishing on Google Play](#publishing-on-google-play)),
and Play never gives it back, so the keystore is backed up off GitHub. A
debug build is signed with the SDK's debug key, which each machine makes
for itself: a debug build and a release build cannot update each other,
and neither can debug builds from two machines.

## Tests

### On the JVM

`bash ./gradlew :app:testDebugUnitTest`, from `android/`, runs the pure
parts with the real `org.json`. The Android constants they rely on (the
haptic feedback values, MotionEvent's and KeyEvent's, WebViewClient's
error codes) are written out in the code and held to `android.jar`'s by
the tests.

- **Ported from the iPad's XCTest suite, case for case where a case
  applies**, in Android's words: `BridgeTests` (the messages, the claim's tool, the declared
  flags), `ShellTests`' address, route and origin rules, `ChromeTests`
  with `ShellStateTests` and `LauncherTests` (who may call, the state,
  the launcher's rules), `CloudSignInTests`, `OpenedFilesTests`,
  `PencilTests` (claims, sampling, the hold, the lazy brush, the ribbon,
  the ruler, `HeldShape` and `ShapeHold`), `InkWidthTests` and
  `ServerTests`. Each pen port reads the shared fixtures the web's and
  the server's tests read, `tests/shared/inkwidth.json` and
  `inkhold.json`, every row, so a change to either runs `android.yml`.
- **Android's own**: the router's rules (`PenRouterTest`), the capture
  (`PenCaptureTest`), the live line's settled and tail parts, widths and
  shade against the whole line frame by frame (`LiveLineTest`), the
  button clicks, the pose and the haptics; the port's framing and the
  sends tied to their document (`HostWireTest`); the chrome's calls; the session's reading;
  the shortcuts and Back; the failure sentences; the saves, the save
  names and the file types; the cloud sign-in's requests and the fetch
  against a fake HTTP server on a free loopback port, with what a save's
  hops may reach (`SaveHops`), and the loopback listener over real sockets; the local server's state machine with the
  time given (`LocalServerTest`), its port rule, environment, password,
  seal, health rule, state file and web app unpacking, two at once
  included.

### On a device

`LocalServerStartTest` (`app/src/androidTest/`, `bash ./gradlew
:app:connectedDebugAndroidTest`) ports the iPad's `LocalServerStartTests`:
it starts the embedded server for real in the app's process, on a data
directory of its own (`cache/start-test/Gamma/local`, deleted at each
run), with a vault of its own (`gamma.start-test`) and a seed for a vault
that refuses, so the person's library is never touched. It waits up to
three minutes for `/api/health`, signs in, uploads a one-page PDF it
builds, makes the page for it, reads the manifest (`/api/pdf-info`, one
page of 612 × 792 points) and asks for a page picture
(`/api/ai/page-image`). Both come from PyMuPDF, which the server imports
at its first PDF read (`/api/health` proves only the start), so they are
the proof that the wheels load and render; a call that does not answer as
expected fails with the server's log tail. It skips when the app starts its own server
(its last server opened was This device), since an interpreter starts
once per process. It asks the app's server, which is already starting
before any test runs, rather than the interpreter, which starts only once
the web app is unpacked. It passes on an Android 16 emulator.
`android.yml` runs it on an x86_64 emulator for every pull request that
touches the app.

### The web side and the server

- **The `android` browser group** (`frontend/tests/e2e/scenarios/android.mjs`,
  with the stand-in shells of `tests/e2e/shells.mjs`) runs the app's
  `host.js` as shipped over a stand-in `gammaAndroidHost` port carrying
  JSON strings both ways, in a tablet-sized touch context: the host
  declared and spoken to through `post`, the theme, the layout by
  orientation, no install offer; a stylus stroke claimed and committed
  from the host's samples with their tilt, a cancelled one saving
  nothing, the pen's two buttons switching tools with their ticks; a
  declined claim committed by the page; a hold snapping a still line; the
  Stylus settings gated by the gestures and the haptics the host
  declares, and no *Lock angle* without a roll; a PDF downloaded in
  base64 chunks at their offsets, the lasso's SVG, a refused save, an
  export's file and a file chip's fetched by address, and nothing
  downloaded by the browser; a PDF handed to the app becoming its page;
  View → *Server bar*; no folder picker; Settings → Server for a server
  inside the app; and the chrome pages under the app's `chrome.js`,
  beside the iPad's and the desktop's. A change to
  `android/app/src/main/assets/shell/`, to `desktop/ui/`, to the ink
  layer, the settings, the opened files, the folder zip, the file chip or
  the tasks selects it (`npm run e2e -- --changed`).
- `frontend/tests/host.test.mjs` runs the real `host.js` over a fake
  port, and `frontend/tests/saveFile.test.mjs` the saves' chunks, their
  refusal, a cancel and the names.
- `backend/tests/test_mobile_entry.py` (the entry, its supervisor and its
  Android-only parts) and `test_mobile_requirements.py` (the pins keep up
  with the server's); `tools/check_mobile_pins.sh` resolves the pins. A
  Windows Python fails that check, on `mcp`'s Windows-only `pywin32`.

A change under `android/` runs the JVM tests and a build; a change to the
page-side scripts or the chrome pages runs the `android` browser group
too:

```bash
# each from the repository's root
cd android && bash ./gradlew --no-daemon :app:testDebugUnitTest :app:assembleDebug
cd frontend && node --test tests/host.test.mjs tests/saveFile.test.mjs && npm run e2e -- --group android,ipad
```

A change to the server's dependencies or its entry, or to anything the
packaged server loads natively, needs the device test on an emulator
([android/README.md](../../android/README.md#the-device-test)), which
`android.yml` runs on pull requests ([github_actions.md](github_actions.md#androidyml)).
Other backend and frontend changes reach This device at the app's next
build, as the same code the other checks test.

## Release

The Android app is released with the desktop app, by `desktop.yml`
([github_actions.md "`desktop.yml`"](github_actions.md#desktopyml)),
dispatched from `main` (the `release` skill):

- **The build.** The `android` job builds the APK and the bundle (AAB)
  once per release from the web app the `frontend` job built, for 64-bit
  ARM only, with the release's version as `versionName` and its
  `versionCode`. With the `ANDROID_*` secrets both are signed with the
  release key, which is also the key Google Play signs its installs with
  ([below](#publishing-on-google-play)), so the GitHub APK and Play's
  builds have one signer. The job checks the APK's 16 KB alignment
  (`zipalign -c -P 16`, which Google Play requires) and, when it is
  signed, its signature. Without the secrets the build is unsigned, and
  none of it is published.
- **GitHub.** A signed APK goes on the release as
  `Gamma-<version>-android-arm64.apk`, and the notes get an **Android
  app** section with its download button, a Google Play button once the
  `PLAY_URL` variable is set (only when the app is public on production),
  and how to install and update it: install the next release's APK over
  it. gammapdf.com's `/android` and `/download/android` lead to the
  latest release, and the site offers the APK to a visitor on Android
  once a release carries one ([sites/README.md](../../sites/README.md)).
- **Google Play.** The bundle is the `store-android` artifact, kept 30
  days. The `play` job ([github_actions.md "The store jobs"](github_actions.md#the-store-jobs))
  sends it to Google Play's internal track, for a release from `main`
  that is not a pre-release and was signed, once the service account's
  secret exists; a refusal is a warning, never a failed release. While
  `PLAY_RELEASE_STATUS` is `draft`, what it sends waits on the track as a
  draft release until someone rolls it out by hand in the Play Console.
  Promotion to the closed and production tracks is done by hand there too
  ([below](#publishing-on-google-play)).
- **Pull requests** touching the app run `android.yml`: the JVM tests, an
  unsigned 64-bit ARM release build checked for 16 KB alignment, and the
  device test on an emulator ([github_actions.md "`android.yml`"](github_actions.md#androidyml)).
  `check.yml`'s `mobile-pins` resolves the pins.

## Publishing on Google Play

The Play Console account is the developer's personal one. The commands
and Console steps for setting it up are in
[github_actions.md "Google Play, once"](github_actions.md#google-play-once),
the listing, the declarations and the checklist from the account to
production in [android/STORE.md](../../android/STORE.md), and what Play's
policies ask of this app in
[research/android.md "Publishing"](../research/android.md#publishing).
What follows was checked against Google's pages on 2026-11-03, and each
point names its source; the Console renames things, so what it shows
wins over the labels here.

- **The account.** Besides the US$25 fee and identity verification, a
  personal account must pass device verification (the Play Console
  mobile app, signed in as the owner on a physical Android 10 or later
  device) before it can publish, and make a trader declaration when it is
  created ([required information](https://support.google.com/googleplay/android-developer/answer/10841920),
  [device verification](https://support.google.com/googleplay/android-developer/answer/14316361),
  [trader status](https://support.google.com/googleplay/android-developer/answer/17594752)).
- **The closed test.** A personal account created after 13 November 2023
  reaches production only after a closed test with at least 12 testers
  opted in continuously for the 14 days before it applies. A tester who
  opts out and back in starts again, and the application asks for a
  summary of the testers' feedback
  ([production access](https://support.google.com/googleplay/android-developer/answer/14151465)).
  Someone opted into the internal test does not count for the closed one
  until they leave the internal test
  ([testing tracks](https://support.google.com/googleplay/android-developer/answer/9845334)).
  So CI feeds the internal track, a release is promoted to the closed
  track by hand for testers kept off the internal list, and the
  application for production comes after the 14 days. The first
  production release cannot be staged: it goes to every country chosen
  at once ([staged rollouts](https://support.google.com/googleplay/android-developer/answer/6346149)).
- **Play App Signing: the release key.** A new app is enrolled by
  default in keys Google generates (hybrid signing), and the key that
  signed the first upload becomes only the upload key
  ([Play App Signing](https://support.google.com/googleplay/android-developer/answer/9842756)).
  Gamma takes the other route Google documents: the developer's own
  release key, supplied to Play App Signing at the first internal
  release, so Play signs its installs with the key CI signs the bundle
  and the GitHub APK with, and both channels have one signer. In that
  release's *App integrity* section, **Change app signing key** →
  provide a copy of the app signing key: the dialog offers Google's PEPK
  tool and an encryption key, PEPK writes the key encrypted into a zip,
  and the zip goes up before the bundle (the command is in
  [github_actions.md "Google Play, once"](github_actions.md#google-play-once)).
  With no separate upload key registered, the release key is the upload
  key as well ([app signing](https://developer.android.com/studio/publish/app-signing)).
  - **When it is fixed.** The key can be changed until a release rolls
    out on open testing or production, and never after. A change after
    an internal or closed release makes those testers uninstall and
    reinstall ([prepare and roll out](https://support.google.com/googleplay/android-developer/answer/9859348)).
  - **What one signer gives.** Android installs one over the other when
    the certificate matches and the `versionCode` is equal or higher, so
    a GitHub APK and Play's build replace each other without losing This
    device's library. Play itself updates only an install that a Google
    account on the device got from Play: someone who installed only the
    GitHub APK gets no updates from Play until they install it from there
    ([app updates](https://developer.android.com/google/play/app-updates)).
  - **The key cannot be recovered.** Play never gives the app signing
    key back, and without it no package can be registered for developer
    verification ([FAQ](https://developer.android.com/developer-verification/guides/faq)),
    so the keystore is backed up off GitHub like the Azure and Apple
    signing secrets. If it were lost, an upload key reset in the Console
    would let CI upload with a new key, but the GitHub APK would then have
    to be Play's universal APK, as in the alternative below.
  - **Hybrid signing.** Google does not say whether an app whose key was
    supplied is still enrolled in hybrid signing. Android 17's notes say
    a hybrid identity needs a new classical key, which Play will offer as
    an upgrade ([Android 17](https://developer.android.com/about/versions/17/features)),
    and after such an upgrade Android 13 to 16 check the latest classical
    key and Android 17 the hybrid one
    ([Play App Signing](https://support.google.com/googleplay/android-developer/answer/9842756)).
    Read together, a GitHub APK signed only with the release key could
    then stop updating Play's installs on those versions. That is the
    research's reading, not something Google states; accepting a key
    upgrade is a decision about the GitHub APK too.
  - **The alternative: Google's key.** Changing nothing at the first
    release keeps Google's hybrid-signing key, with the release key as
    the upload key only. The two channels then share a signer only if
    the GitHub APK is Play's own universal APK (*Latest releases and
    bundles* → the bundle → *Downloads*, or the API's
    `generatedapks.list` and `generatedapks.download`;
    [generated APKs](https://developers.google.com/android-publisher/api-ref/rest/v3/generatedapks/list)).
    In `desktop.yml` that means the `publish` job no longer attaching the
    CI-signed APK, and a step after the `play` upload fetching Play's
    universal APK for that `versionCode` and adding it to the release
    (`gh release upload`), with the notes' Android section written for an
    APK that arrives later. The service account would need more than
    testing tracks (probably *Release to production, exclude devices, and
    use Play App Signing*; not confirmed), and whether Play makes the APK
    for a draft release of a draft app is not known. It would keep future
    key upgrades the same on both channels.
- **The first upload by hand.** The first bundle, a release run's
  `store-android` artifact, is uploaded in the Console (Internal testing
  → Create new release), since that is where the app signing key is
  supplied, and because Google's API guide asks for one upload through
  the Console before the API can be used
  ([edits](https://developers.google.com/android-publisher/edits)).
  Community reports say an app takes only draft releases from the API
  until its first review is approved
  ([Expo forum](https://forums.expo.dev/t/google-api-error-invalid-request-only-releases-with-status-draft-may-be-created-on-draft-app/57027),
  [r0adkll #222](https://github.com/r0adkll/upload-google-play/issues/222)),
  so `PLAY_RELEASE_STATUS` is `draft` until the closed test's release
  passes review. Each upload CI makes then waits on the internal track as
  a draft and is rolled out by hand in the Console; Google documents only
  that drafts made through the API are "for later deployment through the
  Google Play Console"
  ([tracks](https://developers.google.com/android-publisher/tracks)).
- **No upload during a review.** The API's commit cancels a review in
  progress and sends the app again by default
  ([edits.commit](https://developers.google.com/android-publisher/api-ref/rest/v3/edits/commit)),
  and the `play` job runs on every release once its secret is set, so a
  desktop release while Play reviews the app restarts that review. A
  release that cannot wait goes out with `PLAY_SERVICE_ACCOUNT_JSON`
  deleted, and the secret is set again after the review.
- **The service account** lets the `play` job upload: a Google Cloud
  project with the Google Play Android Developer API enabled, a service
  account in it with no Cloud roles and a JSON key, invited in the Play
  Console's *Users and permissions* with *Release apps to testing tracks*
  for this app, its key in the `PLAY_SERVICE_ACCOUNT_JSON` secret
  ([getting started](https://developers.google.com/android-publisher/getting_started),
  [permissions](https://support.google.com/googleplay/android-developer/answer/9844686)).
  The project belongs to the developer's personal Google account: under
  a gammapdf.com Google Workspace or Cloud Identity organisation created
  on or after 3 May 2024, creating service-account keys is blocked by
  default ([secure-by-default organisations](https://docs.cloud.google.com/resource-manager/docs/secure-by-default-organizations)).
- **Developer verification.** Creating the app in the Console registers
  `com.gammapdf.gamma` to the account for Android developer verification
  ([Play Console](https://developer.android.com/developer-verification/guides/google-play-console)).
  Whether that records a key the developer supplied is not confirmed, so
  after the first release the release key's SHA-256 is checked on the
  Console's *Android developer verification* page, under *Package
  names*, and added with **Add key** if it is missing. Play may ask for
  proof: an APK signed with that key that carries
  `assets/adi-registration.properties`
  ([add a key](https://support.google.com/googleplay/android-developer/answer/16762301)).
  A user other than the owner needs the permission *Register and manage
  package names for Android developer verification*. Sideloaded installs
  are not checked yet; certified devices everywhere enforce it from 2027
  ([FAQ](https://developer.android.com/developer-verification/guides/faq)).
- **Policy points**, from the policy register in the research note; the
  details and the open questions are in
  [android/STORE.md "Before the first review"](../../android/STORE.md#before-the-first-review):
  - Inside the apps (any native host) the web app leaves out Settings →
    Account & sync's *Open account* and a hosted server's *Manage plan*:
    account.gammapdf.com's *Plan & billing* and plans pages lead to
    Stripe Checkout, and Play's Payments policy forbids linking to "a
    webpage that could lead to an alternate payment method"
    ([payments](https://support.google.com/googleplay/android-developer/answer/10281818)).
    For the same reason the account server's sign-in and sign-up pages,
    in the browser the app's Gamma Cloud sign-in opens, show their brand
    without its links to gammapdf.com and the account home, and lead a new
    account back to the sign-in
    ([cloud_accounts.md "From a native app"](cloud_accounts.md#the-oidc-provider)).
    Their terms and privacy policy line still links to gammapdf.com/terms/
    and /privacy/, whose header links *Pricing*.
  - Play counts a Gamma Cloud account as one the app lets the person
    create, since the sign-in's browser offers sign-up, so the app needs
    an in-app way to start deleting it and a public web page for the Data
    safety form ([account deletion](https://support.google.com/googleplay/android-developer/answer/13327111)).
    The page is <https://gammapdf.com/delete-account/>, and the app links
    to it from Settings → Account & sync. Deleting the account deletes its
    library on the shared server and its published pages within the hour
    ([cloud_accounts.md](cloud_accounts.md) "A deleted account on the
    share host"), since Play does not count closing them as deletion.
  - The privacy policy is linked in the Console and inside the app,
    among its help links ([User Data](https://support.google.com/googleplay/android-developer/answer/10144311)).
  - Reviewers get a reusable Gamma Cloud test account under App content →
    *Sign-in details*, since the sign-in is a part of the app restricted
    by a login.
  - User-generated content, in case Play's policy applies
    ([UGC](https://support.google.com/googleplay/android-developer/answer/9876937)):
    the Gamma Cloud sign-in and sign-up pages say that continuing accepts
    the terms and the privacy policy, and [TERMS.md](../../TERMS.md) says
    what may not be shared and how it is reported. A comment's "⋯" menu
    and the share view's View menu (a published page is one) have
    *Report*, which reaches the server's admins and, on a server with
    Gamma Cloud sign-in, the developer
    ([cloud.md "Reports"](cloud.md#reports)). A report names the author
    (a comment's, a share's maker), which is how a user is reported;
    there is no blocking of one user by another.
  - AI-generated content, in case Play's policy applies
    ([AI content](https://support.google.com/googleplay/android-developer/answer/13985936)):
    every AI reply in the chat has *Report this reply*, which sends its
    text and model to the server and on to the developer without leaving
    the app. This device's server signs in with Gamma Cloud, so its
    reports reach the developer whether or not the person has an
    account; a report made on a server the person added that runs without
    Gamma Cloud reaches only that server's admins.
  - Still open: whether those two policies apply to the app at all, and
    whether review asks for blocking between users.
- **`PLAY_URL`** stays unset until the app is public on production. Only
  the testers on a closed test's lists can use its opt-in link, which
  appears only once the closed release has passed review, so the release
  notes would send everyone else to a page they cannot use
  ([testing tracks](https://support.google.com/googleplay/android-developer/answer/9845334)).

## Device checks

Checked on an emulator (an Android 16 tablet image, WebView 133):

- The debug app installs and starts. The launcher shows **This device**
  with "App storage › Gamma › local" and "Gamma for Android <version>".
- This device's server starts in about seven seconds on a warm emulator,
  and the silent sign-in lands on the library as `admin`.
- `LocalServerStartTest` passes: health, sign-in, the PDF's upload, its
  page, `pdf-info`, and a page picture from PyMuPDF.
- A stylus stroke (`adb shell input stylus`) with a pen armed is claimed
  by the page, drawn by the front-buffered live line, delivered as one `stroke` and
  committed. Draw and hold snaps a held straight line: `hold`, then
  `{shape: line}`, the haptic, and the `stroke` with `held`. A stylus tap
  is declined (the claim comes after the touch ended), and the page draws
  its own dot. A finger's swipe scrolls the page and claims nothing.
- `save`, `saveData` and `saveDone` write `Download/Gamma/<name>` through
  MediaStore and show the banner.
- During development: Ctrl+Shift+B and Ctrl+Shift+L; Back closing the
  bar's menu and the launcher; rotation and the system turning dark
  keeping the document; a crashed renderer's WebView replaced with the
  bridge and the session kept; a failed start of This device shown in
  the launcher with its log.

Still to check, on the emulator where it can show them and on real
hardware (an S Pen tablet, a USI pen, a Wacom EMR device):

1. **The live line's latency** with a real pen: it keeps up with fast
   writing, with no tearing or flicker at the tip (the front buffer is
   redrawn region by region while the display reads it), and the
   prediction's overshoot is acceptable. A ten-second scribble stays
   smooth: each event recomputes the stroke's widths on the main thread.
2. **Pressure** is read only where the device reports a pressure range;
   a pen without one draws at 0.5.
3. **Tilt.** The samples' altitude and azimuth against the WebView's own
   `altitudeAngle` and `azimuthAngle` for the same pose, on a stroke the
   page did not claim (`Tilt`'s formulas are derived, not measured).
4. **Palm rejection.** A palm down before and during writing; the
   system's `FLAG_CANCELED` abandoning a stroke (`stroke` with no
   samples); and, on a device whose stylus and touchscreen are separate
   input devices, a palm that must not abandon a captured stroke (a
   gesture is one device's here).
5. **The buttons.** A click while hovering sends `pencil`, and the first
   button held as the pen touches down erases; whether Samsung's Air Command takes hover
   clicks; whether a real pen's button keys reach the app at all (the
   emulator's `input keyevent` did not); the eraser end erasing; the tail
   button left to the system.
6. **Hover** still moves the page's cursor, and Android's stylus
   handwriting into the page's text fields still works.
7. **A highlighter multiplied** onto the page through the View on a real
   GPU, or at its plain opacity if the WebView composites apart.
8. **The surface.** The see-through `SurfaceView` shows nothing at start,
   after rotation and with the keyboard up; a device without front-buffer
   support still draws, through the View; a line still showing (up to
   three seconds after a lift) draws over a launcher or menu opened
   meanwhile.
9. **On the emulator, not yet run:** many strokes back to back
   (graphics-core's commit counter); a finger down, then a stylus claimed
   (the page cancels both and does not scroll), and a second stylus with
   the finger still down; a stylus down with its first button held
   erasing, with no claim and no `pencil`; strokes after a renderer
   crash; a hold answered `cancel` (*Paste here*).
10. **The soft keyboard.** The frame ends above it and the page resizes
    (the emulator had a hardware keyboard).
11. **Gamma Cloud sign-in:** an Auth Tab where the browser has one, a
    Custom Tab where it has not, the return through
    `gamma-android://cloud-signin` closing the tab, Google's sign-in, a
    Link from Settings, and a second tap while the browser is open.
12. **The file chooser:** the Photo Picker for a picture, the document
    picker with the input's types, several files, a `.goodnotes`, a
    folder input as files; Android 10 and 11 without the system Photo
    Picker.
13. **The microphone:** the system's question, a recording, dictation, a
    refusal, and the question open while the activity is made again.
14. **Opened files:** Files' *Open with* and the share sheet for each
    type, the note apps' exports through the share sheet, a file shared at
    a cold launch, one reopened from Recents not taken twice, and the time
    a large PDF takes over the bridge. A tap on the app's icon while the
    sign-in's tab or a picker is open, before and after a file was opened
    in the app, must bring them back as they were, with one window.
15. **`saveUrl`:** an export's file and a file chip's fetched with the
    server's cookie; MediaStore's " (1)" for a name taken; *Open* in
    another app; a `blob:` download outside the bridge.
16. **Load failures:** a certificate error and the WebView's error page
    each reported once; a window's colour after the system turns dark
    before any page has painted.
17. **HEIC** from the Photo Picker: whether the WebView decodes it, or
    the page asks for a JPEG or PNG as in Chrome.
18. **A phone:** the web app's compact layout under the bar, in both
    orientations. Portrait ran on a 1080 × 1920 emulator (Pixel 2, API 36)
    for the Play screenshots (`tools/readme-media/shoot-play.mjs`): the
    reader, the notes, the pen, the chat, the library and the search work
    under the bar. The launcher's This device card leaves its name no room
    on that width (the badges, Open and the four icon buttons fill the
    row), and the viewer's tool rail stands over the page's left margin at
    the page's width.
19. **64-bit ARM.** The two Android-only fixes in `gamma_mobile.py` (the
    wheels' libraries loaded by path, PyMuPDF's untagged extensions) were
    made and checked on the x86_64 emulator only: a real `arm64-v8a`
    device must open This device and read a PDF, or run the device test.
20. **The first start on a real device** after an install: how long the
    extraction takes, and the memory of the server and the WebView
    together.
21. **The background:** the system ending the process while the app is
    away, the restart when it comes back, and a clone's sync.
22. **Backup and transfer:** the library, the password and Chaquopy's
    files left out (`bmgr backupnow` within the cloud quota), the servers
    listed brought along.
23. **The workflows.** `android.yml`'s two jobs and `desktop.yml`'s
    `android` and `play` jobs have not run on GitHub (actionlint passes
    them); the first signed release and the first Play upload are still
    to come.
24. **The iPad after the move** of its entry to `backend/gamma_mobile.py`
    and its pins to `backend/requirements-mobile.txt`: `ipad.yml` must run
    on a Mac.
25. **A stylus with nothing armed on a PDF page.** A drag selects text
    without panning and the popup opens at its lift, a tap clicks a link
    (the page's own click), a palm on the page, on the next page or in a
    gap neither pans nor pinches during the drag, and a pen held still
    starts no system word selection. Blink shows no handles for a
    selection set from script, so the page's popup is all that finishes
    one. The cost of unbuffered dispatch on such a stroke, which no claim
    follows.

## Not built yet

- **Background running.** There is no foreground service: the server
  runs while the app's process does, and Android may end the process in
  the background, so a clone syncs only while the app is open. One would
  need a declared service type and Play's review
  ([research/android.md](../research/android.md#the-policy-register-for-this-app)).
- **A Play release.** The app is not yet in the Play Console; the first
  upload, the closed test and the production application are to come
  ([Publishing on Google Play](#publishing-on-google-play)).
- **The iPad's other gestures.** A stylus's buttons have no squeeze
  phases and Android reports no barrel roll, so the squeeze's *while
  held* choices and a calligraphy nib turned by the roll are the iPad's
  alone.
- **The camera from a file input**, and HEIC converted by the shell: the
  web app asks for neither.
- **A folder picked as a folder**: Android's pickers return files.
- **The Photo Picker's backport** for Android 10 and 11 is not declared;
  androidx falls back to the document picker there.
- **The clone chips** on the bar's workspace rows, as on the iPad
  ([ipad.md "Not built"](ipad.md#not-built)).
- **Moving data between servers**: nothing moves a library from This
  device to another server or back, beyond clones and the web app's
  per-workspace Export and Import.
