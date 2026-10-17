# Gamma on the iPad (and other tablets)

There are two ways to use Gamma on an iPad, and both are the same web app:

- **The iPad app** (`ipad/`, [ipad/README.md](../../ipad/README.md)) is a
  shell over Gamma's own web app. It shows it in one `WKWebView` and adds
  what Safari cannot: Apple Pencil strokes captured natively, the
  Pencil's double tap and squeeze, a Gamma Cloud sign-in that works with
  Google, and a Gamma server of its own inside the app, so it works with
  no connection, as the desktop app runs its own
  ([The local server](#the-local-server)). Why it has this shape:
  [research/ipad-web-shell.md](../research/ipad-web-shell.md).
- **The web app installed to the home screen** is every feature, online,
  with Safari's own pen. It is described under
  [The installed web app](#the-installed-web-app).

Handwriting is the same `gamma-ink` file and the same block properties
whichever wrote it ([handwriting.md](handwriting.md)): in the app, the web's
ink layer still encodes and saves every stroke.

## The native app

### What the user does

1. Pick a server. The first launch shows the server picker: **This
   iPad**, the server inside the app, or another server by its address (a
   NAS, a hosted Gamma). The app loads that server's page.
2. Sign in. On This iPad the app signs in by itself with the account it
   made ([below](#the-account-and-the-silent-sign-in)). On another server
   the person signs in on the web app's own login page, with a password or
   Gamma Cloud.
3. From then on everything is the web app: the library, the notes, the
   PDF viewer, settings, sharing. The Pencil writes through the native
   capture ([The pen](#the-pen)), and its double tap and squeeze do what
   the system's Pencil settings ask for.
4. **Server…**, in the account menu and on every page shown before the
   library (the sign-in page, an unavailable workspace, a blocked share
   link), brings the picker back. When a server cannot be reached, the
   app shows a page of its own with Try again and Choose a server;
   choosing the same server again retries.
5. For work away from the network, a workspace of another server is
   cloned onto This iPad in the web app's settings
   ([Offline use](#offline-use-clones)).

The Swift side holds no page, stroke, tool or token. The one secret it
keeps is the local server's seeded password. The session is the web
view's cookie (the default data store keeps it across launches), and
the library is the server's, This iPad's included.

### How it is put together

The web app is the one owner of the page. Native code is a window, an
input device, a preview layer and, for This iPad, a process manager, and
nothing in Swift knows what a block is. So a web feature reaches the iPad
when the server has it (This iPad's at the app's next build), with no
iPad work. The two halves share only the host contract below. The files
are listed in [ipad/README.md](../../ipad/README.md).

- **The window.** `ShellView` hosts one controller (`ShellController`),
  made once and kept: a new server or a retry loads into the same web
  view. Its container fills the screen and is painted with the theme's
  background, so the strip under the status bar is the page's colour. The
  web view starts below the status bar (pinned to the safe area's top)
  and runs under the home indicator, as the page's CSS expects
  (`html[data-shell] .app` pads only the bottom, [below](#what-the-install-adds-and-where-it-lives)).
  The live stroke view keeps exactly the web view's frame.
- **One CSS pixel is one point.** The viewport is at scale 1
  (`width=device-width, initial-scale=1`), the document never scrolls
  (`html, body, #root` fill the viewport; the panes scroll), and the web
  view has `preferredContentMode = .desktop` (an iPad mini would get the
  phone's layout otherwise), no bounce and
  `contentInsetAdjustmentBehavior = .never`. So a point in the web view
  is the page's `clientX`/`clientY`, which the pen's places and samples
  rely on.
- **Navigation** (`Navigation.swift`). The server's own pages stay in the
  web view. Another origin, and `target=_blank`, open in a Safari view.
  Other schemes (`mailto:`) go to the system. A download goes to a
  temporary file and then the share sheet. The microphone is granted to
  the server's page for recordings. The Gamma Cloud start is taken over
  ([below](#gamma-cloud)). When the system ends the page's process, the
  shell reloads, and the web app restores itself as after any reload.
- **Theme.** The page posts `theme` where it sets `theme-color`. The shell
  paints the colour under the page and behind it, keeps it for the next
  launch so the app opens in the colour it closed in, and gives the window
  the page's scheme for the status bar's style. The web view itself stays
  at the system's style, so a page on the "system" theme still reads the
  system's preference.

### The host contract

Declared by a `WKUserScript` at document start, before the bundle runs
(`HostInfo` in `Bridge.swift`):

```js
window.gammaNative = {
  platform: "ipad",   // the host
  version: "0.2.0",   // the app's CFBundleShortVersionString
  ink: true,          // the host captures claimed Pencil strokes
  pencil: true,       // the host forwards the Pencil's double tap and squeeze
  servers: true,      // the host has a server picker (the web shows "Server…")
};
document.documentElement.setAttribute("data-host", "ipad");
```

The web half is one module, `frontend/src/shared/lib/host.js`:
`nativeHost()` (the object or null, read once), `postToHost(msg)` (a
promise of the reply; it rejects with no host) and
`onHostMessage(type, fn)`. It also sets `data-shell` on `<html>`, for the
installed app and the shell alike, which the full-screen CSS keys off.
Nothing else in the web app knows a host exists.

Web → app: `window.webkit.messageHandlers.gamma.postMessage(msg)`
(`WKScriptMessageHandlerWithReply`, the page content world). The shell
takes messages only from the server's own main frame.

| Message | Reply | Meaning |
|---|---|---|
| `{type: "claim", pointerId, at: {x, y}, tool: {kind: "pen"\|"highlighter", color, size, opacity}}` | `{taken}` | This Pencil touch is a stroke: take it. `at` is where the pointer went down, in viewport CSS px; `size` is the width on screen, in CSS px |
| `{type: "theme", bg: "#rrggbb", scheme: "light"\|"dark"}` | `{}` | Paint the window behind the page and pick the status bar's style |
| `{type: "servers"}` | `{}` | Show the server picker |

App → web: `window.gammaNative.receive(msg)` through
`callAsyncJavaScript`, which waits for the returned promise. The user
script defines a stub that queues until `host.js` takes over and delivers
the queue, so nothing sent early is lost.

| Message | Resolves | Meaning |
|---|---|---|
| `{type: "stroke", pointerId, samples: [[x, y, p, t], …]}` | `{committed}`, two frames later (React's commit and a paint) | The claimed stroke ended. x, y in viewport CSS px; p = force / maximum force, the lift keeping the last pressure; t = ms from the first sample, from the hardware timestamps, never going back. No samples: the system cancelled the touch, and the claim just ends |
| `{type: "pencil", gesture: "tap"\|"squeeze", action}` | at once | `action` is the system's preferred action: `switchEraser`, `switchPrevious`, `showColorPalette`, `showInkAttributes`, `runSystemShortcut` or `ignore` |

Keeping it small is the point. A message is added in `Bridge.swift` and
`host.js` together, and `version` tells the web which host it runs under.
A message type nobody listens for resolves at once on the web side, so a
newer shell never hangs an older page; a message the shell does not know
gets an error reply.

### The pen

The web decides, native executes. On a Pencil touch the web's ink layer
decides what the touch is, as it does in Safari: a stroke, a tap, the
eraser, the lasso, a move, the hand. Only a pen or highlighter stroke is
claimed.

1. **The touch begins.** `PencilCapture`, a Pencil-and-direct recognizer
   on the web view's container, records the touch from its first sample
   (the coalesced touches, every sample the Pencil measured). It delays
   nothing and recognizes alongside every other recognizer, so the page
   gets the touch as in Safari.
2. **The page claims it.** `InkLayer.jsx` starts its stroke as always,
   draws its own draft, and posts `claim` with the pointer's id, where it
   went down and the tool (`claimForHost`).
3. **The shell matches the claim to a touch** (`ClaimQueue`). The page's
   pointer id means nothing natively, so a claim takes the oldest Pencil
   touch that began within 10 points of `at`. A claim whose touch already
   ended takes nothing (a dot the page's own `pointerup` finished first),
   and an ended touch is remembered for half a second, so a late claim
   cannot take the next touch at the same spot. A touch nobody claimed (a
   tap on a button, the eraser, the lasso) only stands in the way of a
   claim at that same spot. A touch going down forgets the long-ended ones.
4. **Taken.** The recognizer begins. That prevents WebKit's recognizers
   still deciding (its touch events, pans, long presses: `canPrevent` is
   true, `canBePrevented` false) and cancels the web view's touch
   (`cancelsTouchesInView`). While it captures, it swallows fingers on the
   screen (the palm). It also draws the live line (`LiveStrokeView`): a
   plain polyline at the tool's colour, width and opacity, extended by the
   predicted touches.
5. **The page gives way on the reply.** On `{taken: true}` the layer drops
   its draft without saving (`handOver`), ignores that pointer's own
   events, and treats fingers as palms until the stroke ends. It does not
   wait for a `pointercancel`, which WebKit may not send. So a stroke is
   never saved twice, and the page never pans under the Pencil.
6. **The lift.** The shell sends `stroke` with the samples. The layer
   converts them with the rect and zoom of the down and applies the
   repeated-point rule of `appendInkSample`. It commits through the same
   `encodeStroke` and `onStroke` as its own strokes (`ch: "xypt"`, `t0` the
   web's wall clock at the down). Undo, replay and saving are unchanged.
   The live line stays until the reply, which comes after the page has
   painted the stroke, so the swap never blinks. A page that never
   answers loses the line after three seconds.
7. **Cancelled.** When the system cancels a captured touch, the line goes,
   and the shell sends `stroke` with no samples, which ends the claim and
   saves nothing.

A claim the shell does not take (`{taken: false}`) leaves the touch to the
page, which finishes it as in Safari. Pencil hover, pressure and tilt
reach the page as Safari gives them.

**Gestures.** `PencilGestures` (`UIPencilInteraction`) forwards the double
tap and, on iPadOS 17.5 and later, the squeeze, as `pencil` with the
system's preferred action. `App.jsx` maps it onto the
markup toolbar's own handlers: `switchEraser` toggles the eraser,
`switchPrevious` picks the previous preset, `showColorPalette` and
`showInkAttributes` open the active tool's options row, and the rest do
nothing.

### Gamma Cloud

Google refuses to sign in inside an embedded web view, so the page's own
start (`/api/auth/cloud/start`) is taken over, as the desktop app does
(`CloudSignIn.swift`; `desktop/lib/cloudSignIn.js`). The shell listens on
`127.0.0.1:<port>` and posts `app-start` from inside the page, so a Link
carries the page's session, with that return address and an S256 PKCE
challenge. It opens the authorize URL in an `ASWebAuthenticationSession`.
The server sends that browser back to the loopback address with a
one-time result. The listener keeps it and redirects the sheet to
`gamma-ipad://cloud-signin`, which closes it. The web view then posts
`app-claim` with the verifier, which sets the session cookie where the
web app runs. The server needs nothing new. A password sign-in is the
web app's login page.

### The local server

"This iPad" is Gamma's own backend running inside the app on
`127.0.0.1:<port>`, the way the desktop app runs a sidecar server beside
its window ([desktop/docs/architecture.md](../../desktop/docs/architecture.md#servers-and-workspaces)).
The shell shows it like any other server: the web view loads its address,
and the page is the web app that server serves. So the whole web app works
with no connection, and the Swift side stays a process manager
(`Server/LocalServer.swift`). It picks the data directory and the port,
seeds the first account, starts the interpreter and says when the server
answers. Nothing in it knows what the server holds.

#### What runs

- **CPython 3.13 for iOS**: BeeWare's
  [Python-Apple-support](https://github.com/beeware/Python-Apple-support)
  `3.13-b16` (Python 3.13.16), embedded as `Python.xcframework`. CPython
  supports iOS since 3.13 (PEP 730), and the app embeds it as
  [its iOS guide](https://docs.python.org/3/using/ios.html) describes.
- **The backend from `backend/`, unchanged**: `app.py` and the `gamma`
  package, copied into the bundle at build time. The server has no iPad
  branch. It reads the environment the desktop's sidecars are given
  ([desktop.md](desktop.md)), and it spawns no processes, which iOS would
  not allow.
- **The built web app** from `frontend/dist`, served at the server's root
  (`GAMMA_STATIC_DIR`). This iPad therefore shows the web app of the app's
  own build, as a desktop's local server does; another server shows its
  own.
- **`ipad/python/gamma_ios.py`**, the entry, the iPad's
  `desktop/backend_entry.py`. It sends all output to the log, imports the
  server once the environment is final (`gamma/config.py` reads it at
  import), and runs uvicorn on `127.0.0.1` at `GAMMA_PORT`.

`Server/PythonRuntime.swift` runs one interpreter on a thread of its own
(`gamma-server`). The thread has a 16 MiB stack, because a secondary
thread's 512 KiB default is too small for Python's recursion and the
server's imports. The configuration follows the guide:

- UTF-8 mode, whatever the device's locale.
- Unbuffered standard streams, so the log is current.
- No bytecode written, since the signed bundle is read-only.
- No signal handlers: the process's signals are the app's, and this is
  not the main thread.
- Exactly four module paths, all in the bundle: the standard library, its
  `lib-dynload`, `python/app` and `python/app_packages`.

The C API comes in through a bridging header. The interpreter is never
finalized, and it cannot be started twice in one process. So a failed
start stays failed until the app is opened again, and the pages say so.
An uncaught `SystemExit` (uvicorn's way of saying it could not start)
would end the whole app under `PyRun_SimpleString`, so the entry turns it
into an ordinary error.

#### The wheels

The server's dependencies come as iOS wheels, from PyPI and from
[Flet's index](https://pypi.flet.dev), which publishes iOS builds of the
native packages PyPI lacks. `ipad/python/requirements-ios.txt` is
`backend/requirements.txt`, changed only where a package has no iOS wheel
at the server's pin. Each change is commented in the file:

| Package | Server | iPad | Why |
|---|---|---|---|
| uvicorn | `uvicorn[standard]` | `uvicorn` | uvloop and httptools have no iOS wheels. h11 on the asyncio loop serves the same, websockets is listed on its own, and the other extras serve only the development reloader and config files |
| orjson | 3.12.0 | 3.11.9 | Flet's newest iOS build |
| pydantic | 2.13.2 | 2.13.4 | pydantic pins its pydantic-core exactly, and Flet builds only some cores: 2.13.4's (2.46.4) is there, the server's (2.46.2) and 2.14's (2.50) are not |
| cryptography | 50.0.1 | 43.0.1 | Flet's iOS build. The server uses only `cryptography.fernet`, the same in both. It links OpenSSL 3 without the legacy provider and imports only with `CRYPTOGRAPHY_OPENSSL_NO_LEGACY` set, which `gamma_ios.py` does before the server's imports |
| pypdfium2 | 5.11.0 | left out | no iOS build anywhere |

The native wheels are pydantic-core, cryptography (with cffi), orjson,
bcrypt, Pillow, and rpds-py (jsonschema's, through mcp); the rest is pure
Python. `backend/tests/test_ios_requirements.py` fails when the server
gains a dependency the iOS file neither lists nor excludes with a reason,
so each new one gets an iOS decision.

Every pdfium import in the server is guarded, so without pypdfium2 each
use falls back:

- PDF text for search and the AI comes from PyPDF2 (`gamma/pdf_text.py`),
  with worse word spacing. The viewer is pdf.js in the page and is
  unaffected.
- There are no page images: `pdf_text.render_page` answers none, so the
  AI's page and region pictures and MCP's `view_pdf_page` go without.
- A PDF's own highlights import without their quotes.
- The annotated-PDF export puts the notes in the margins (`pdf_notes.py`),
  and the Logseq export leaves out area-highlight images.

A server change that imported pdfium unguarded would break This iPad's
start. The start test ([Tests](#tests)) is what catches it.

#### Where the data lives

The data directory (`GAMMA_DATA_DIR`) is `Documents/Gamma/local` in the
app's container. It is in Documents because that is the folder the Files
app shows (`UIFileSharingEnabled` and `LSSupportsOpeningDocumentsInPlace`
in `project.yml`). There it appears as On My iPad › Gamma › Gamma › local,
with the log, as the desktop app shows its data folder. The picker shows
its size. It is in the iPad's backups like any app's documents, and
deleting the app deletes it.

#### The address

The server keeps its port, by the desktop's rule (`desktop/lib/sidecar.js`
`pickPort`):

1. The port it had last time (`gamma.local.port` in `UserDefaults`), while
   that is free.
2. Otherwise, the first free port in the IANA dynamic range (49152–65535),
   from a random point.
3. Only then, whatever port the system hands out.

A kept port keeps the origin, and with it what the web app stores per
origin (its local storage). The free check binds `127.0.0.1` with
`SO_REUSEADDR`, as uvicorn does. So a port the last run left in
`TIME_WAIT` (the app killed and opened again at once) still counts as
free, while one another socket listens on does not.

#### The account and the silent sign-in

A first start, on an empty data directory, seeds one account: `admin`,
with a random 12-character password (9 random bytes in base64url, the
desktop's shape and the server's own). The password is kept in the
Keychain (`Server/Keychain.swift`). It is readable after the first unlock
and travels in an encrypted backup, so a restored iPad gets it back with
the data directory. The app passes the account as `GAMMA_ADMIN_USER` and
`GAMMA_ADMIN_PASSWORD` only on that start, since the server ignores them
once it has accounts. The picker shows the account and copies the
password, for signing in again after a sign-out.

A data directory whose password the Keychain does not hold gets no silent
sign-in: one restored from a backup without the Keychain, or one whose
first write to the Keychain was refused. The person then signs in on the
page. A password the server made itself, with no account seeded, is in
its log.

The shell signs in to the local server as the desktop does
(`autoLoginScript` in `desktop/main.js`; `WebShell.swift`). After the
first finished main-frame load on the local server's origin, it asks
`/api/session`. When that is anonymous, it posts `/api/login` with the
seeded account and reloads, so the session cookie lands in the web view's
own store.

- It runs through `callAsyncJavaScript` in the app's own content world,
  with the account passed as arguments, so the page's scripts never see
  the password.
- It runs only when the page's origin is the local server's; nothing runs
  in another server's page.
- It runs once per open: a pick in the picker, Try again, or the reload
  after the page's process ended. So a person who signs out stays signed
  out until This iPad is opened again, as on the desktop.

#### The environment

Set with `setenv` before the interpreter starts, since `os.environ` is a
copy taken then (`LocalEnvironment` in `LocalServer.swift`):

| Variable | Value |
|---|---|
| `GAMMA_DATA_DIR` | `<Documents>/Gamma/local` |
| `GAMMA_STATIC_DIR` | `<bundle>/python/frontend_dist` |
| `GAMMA_VERSION` | the app's `CFBundleShortVersionString`, shown as the server's build |
| `GAMMA_CLOUD_DEFAULT_ISSUER` | `https://account.gammapdf.com`: Gamma Cloud sign-in is on until the admin picks another account server or none, as on the desktop ([cloud_accounts.md](cloud_accounts.md)) |
| `GAMMA_PORT` | the port above, read by `gamma_ios.py` |
| `GAMMA_ADMIN_USER`, `GAMMA_ADMIN_PASSWORD` | the seeded account, on a first start only |

`GAMMA_FOLDER_LINKS` is not set: folders kept on disk are the desktop
app's ([folder_sync.md](folder_sync.md#folders-kept-by-the-desktop-app)).

#### Starting, and the log

The server starts at launch when This iPad was the last pick, and
otherwise the first time it is opened. It then runs while the app does,
whichever server the window shows.

- **Before the start**, the app checks that the bundle has the server and
  the web app in it, so a build made without the Python step says so.
- **While it starts**, a page of its own covers the web view
  (`LocalStartView`), and the app asks `/api/health` every 250 ms. It
  counts as up by the desktop's rule: a 2xx, or the 503 with
  `data_directory_not_upgradable`, a server that is up and shows its own
  guidance ([migrations.md](migrations.md)).
- **There is no deadline**, because the interpreter cannot be started
  again in the process: giving up would only hide a server that answers
  later. After a minute the start is marked slow (the first one after an
  install, on an older iPad). The pages then say it can take a few
  minutes, and the polling drops to once a second. Only the interpreter's
  own end makes the start fail.
- **Back from the background.** iOS suspends the app, and the server with
  it, and can reclaim a suspended app's listening socket, notably while
  the screen is locked. When the app is active again, it asks
  `/api/health` up to three times, a second apart. A server that stays
  silent is reported as stopped, with what to do (close the app and open
  it again), rather than left behind a page that cannot load.

Everything the server prints goes to `<data>/logs/server.log`
(`gamma_ios.py`). The file is appended across launches, and each start is
marked with a `--- <UTC time> start port <port> ---` line, the desktop
sidecar's marker. `faulthandler` writes the threads' stacks there before a
crash in native code. A failed start shows its message and the log's last
4 KB in the picker, and the Files app reaches the whole file.

#### Offline use: clones

This iPad is a Gamma server like the desktop's local one. So the offline
copies of another server's workspaces are Gamma's own clones (mirrors,
[mirror.md](mirror.md)), made and resolved in the web app as the
desktop's are ([desktop/docs/architecture.md](../../desktop/docs/architecture.md#clones)).
The Swift side keeps no sync state.

The desktop makes a clone with one click on its switcher's *clone* chip.
The iPad shell has no switcher, so a clone is made the way any Gamma
makes one ([mirror.md](mirror.md#making-one)):

1. On the other server, Settings → Integrations: a read-and-write token.
2. On This iPad, Settings → Workspaces → Clones → *Clone a remote
   workspace*, with that server's address and the token.

From then on the sync pill, the merge chips and the conflict cards are the
web app's. A clone syncs only while the local server runs, that is, while
the app is open and This iPad has been opened in that launch.

#### The server picker

`Views/ServersView.swift` shows at first launch, and whenever the web
app's Server… posts `servers`. It is plain SwiftUI with the system's
styling.

- **This iPad**: a dot for the state (green running, orange starting, red
  failed, grey not started), the address or the state with the data
  directory's size, and Open. A failed start adds its message and the
  log's tail. The account's name and Copy the password sit under it.
- **Servers**: the other servers by address, newest first. Each has a
  reachability dot from `/api/health` (a 3 s timeout, the answer kept for
  30 s, as the desktop caches its remotes'), Open, and Edit and Remove on
  a swipe or a long press. Editing the shown server's address loads the
  new one.
- **Add a server**: the address as typed or pasted, opened at once. A bare
  host means `https`, and a page link keeps only its origin.

The pick and the list are kept in `UserDefaults` as one JSON value
(`gamma.servers`, `AppModel.swift`). The single address and recent list an
earlier version kept become the current server and the list the first
time it is read.

#### How the bundle is made

`ipad/scripts/build-python.sh` runs on a Mac before each Xcode build (how
to run it, its outputs and its cache: [ipad/README.md](../../ipad/README.md#build-and-run)).
It downloads the support package once and checks it against a pinned
sha256. It installs the wheels for the device and for the simulator,
copies the server code and the built web app, and compiles their bytecode
with the Mac's Python 3.13. The device writes no bytecode, so without it
each launch would compile the standard library, the wheels and the
server from source.

After Xcode has copied the resources, a post-build phase in `project.yml`
copies `python/app`, `python/frontend_dist` and the platform's wheels into
the bundle. It then runs the support package's `install_python`. That
adds the standard library and turns each compiled module (`.so`) into a
framework of its own, signed, since iOS loads an app's native code only
from frameworks.

### Tests

- `ipad/GammaIPadTests` (XCTest, on macOS in `ipad.yml`), the pure parts:
  - the messages' decoding and encoding and the user script
    (`BridgeTests`)
  - the cloud sign-in's requests and PKCE (`CloudSignInTests`)
  - claim matching and sampling (`PencilTests`)
  - the address, navigation and colour rules (`ShellTests`)
  - the local server's port rule, environment, password shape, module
    paths and health rule, and the saved servers, an earlier version's
    keys included (`ServerTests`)
- `LocalServerStartTests` (in `ServerTests.swift`) starts the embedded
  server for real in the test host. It checks the bundle's layout first,
  so an unprepared build says what is missing. Then it waits up to three
  minutes for `/api/health`. It is CI's one check that the bundle runs: a
  wrong layout, a broken framework conversion, a missing wheel or an
  import error fails here instead of on an iPad. It skips when the app had
  already started its own server (a simulator whose last pick was This
  iPad), since an interpreter starts once per process.
- `backend/tests/test_ios_requirements.py`: every server dependency is in
  the iOS requirements or excluded with a reason, and nothing excluded is
  still listed.
- `frontend/tests/host.test.mjs`: `host.js` with a fake `window` (the
  queue delivered, replies, unknown types, no host).
- The `ipad` browser group's shell step (`tests/e2e/scenarios/ipad.mjs`)
  fakes the host. A pen down on a PDF page is claimed with its place and
  tool; the reply ends the draft, and the pointer's own events save
  nothing. The host's samples commit as an ink block with the right page
  units, pressures and times, and a stroke with no samples saves nothing.
  The theme message matches `theme-color`, the gestures switch tools, the
  account menu's Server… row and the sign-in page's Server… link post
  `servers`, and no install offer shows.

A change to the bridge (`host.js`, the ink layer's claim, App's theme,
gesture or Server wiring, the sign-in pages, or anything under `ipad/`)
runs:

```bash
cd frontend && node --test tests/host.test.mjs && npm run e2e -- --group ipad,ink
```

and, for `ipad/**`, the XCTest suite on a Mac, which `ipad.yml` does in
CI ([github_actions.md](github_actions.md#ipadyml)). A change to
`backend/requirements.txt` runs `test_ios_requirements.py`, and
`ipad.yml` runs for it too. Other backend and frontend changes reach This
iPad at the app's next build, as the same code the other checks test.
`ipad.yml` does not run for them, so a change that matters on the device
(a pdfium import, a process spawned) is worth a manual dispatch.
`bash ipad/scripts/build-python.sh --check` resolves every pin for both
platforms without Xcode, on macOS or Linux; `check.yml` runs it on every
pull request.

### Device checks

Only an iPad can show these, and none has been run yet:

1. Whether WebKit sends the page a `pointercancel` once the recognizer
   begins, and whether preventing its recognizers stops a pan begun
   meanwhile. The page does not depend on the `pointercancel` (step 5),
   but a scroll that starts before the claim would move the page.
2. The claim's round trip under load, and the 10-point reach against the
   place WebKit reports for the `pointerdown`.
3. Palms during a captured stroke: swallowed by the recognizer, and
   refused by the page.
4. Pointer events in the web view equal to Safari's: pressure, tilt,
   hover, coalesced and predicted events.
5. The keyboard accessory bar under the editing bar, and the visual
   viewport's keyboard behaviour the bar depends on ("Where it sits",
   below).
6. Recording, exports through the share sheet, the file picker and the
   clipboard.
7. The Gamma Cloud sheet: Google in `ASWebAuthenticationSession`, the
   loopback return, the sheet closed by the custom scheme.
8. The swap from the native line to the web's outline.
9. A terminated web content process: the reload and the web app's
   restoration.
10. The local server's first start on a real iPad: how long it takes,
    and the memory of the server and the page's process together.
11. The server after the background and a locked screen: whether iOS
    reclaims its socket, and how often the recheck reports it stopped.
12. A device build signed with a real team: `install_python`'s
    frameworks loading on the device.
13. The data directory in Files, and a restore from an encrypted backup
    bringing the password back with the data.
14. A clone on This iPad syncing with another server, and Gamma Cloud
    sign-in on the local server.

### Not built yet

- **Background running.** iOS suspends the app soon after it leaves the
  screen, and the server with it. So a clone syncs only while the app is
  open, and a server whose socket was reclaimed needs the app closed and
  opened again. Restarting uvicorn inside the running interpreter, without
  running the server's startup again, would make that relaunch
  unnecessary. It is not built.
- **Retrying a failed start** without relaunching the app, for the same
  reason: the interpreter starts once per process.
- **A store release.** The app is built from source with a developer's
  own team. CI signs and uploads nothing, and App Review has not been
  tried ([research/ipad-web-shell.md](../research/ipad-web-shell.md),
  risk 9).
- **pypdfium2**, and with it what [The wheels](#the-wheels) lists.
- **Moving data between servers.** Nothing moves a library from This iPad
  to another server or back, beyond clones and the web app's per-workspace
  Export and Import, as on the desktop.
- **The keyboard accessory bar.** The web view's ‹ › Done bar is not
  hidden, so it sits under the web app's editing bar.

## The installed web app

The home-screen app is the web app installed to the home screen. Its
browser ink layer already gives Apple Pencil pressure, tilt, hover, palm
rejection and Safari's touch-gesture handling
([handwriting.md](handwriting.md)). Every other feature (the block editor,
search, AI, sharing, workspaces) is the same React code the desktop shell
hosts. [research/ipad.md](../research/ipad.md) has the reasoning, and
what the upstream fork's PDFKit/PencilKit app was measured against.

### What the user does

1. Open the server's URL in Safari on the iPad and sign in.
2. Share button → **Add to Home Screen** → Add. Chrome and Edge on a
   desktop or Android tablet offer **Install Gamma** in the address bar
   for the same result.
3. Gamma opens full screen from the icon, without Safari's toolbars,
   signed in as before (the session cookie lives a year; the installed app
   has its own cookie jar, so the first launch may ask once).

Writing then works as everywhere else: a Pencil draws right away, fingers
scroll and pinch, the tool strip's presets are per browser (the installed
app counts as one browser, separate from Safari).

### What the install adds, and where it lives

| Piece | Where | Notes |
|---|---|---|
| The manifest | `frontend/public/media/manifest.webmanifest` | `display: standalone`, `start_url` `/` (the app then picks the last used workspace), the three PNG icons. Unversioned, so under `/media/` (sent `no-cache`, [repository.md](repository.md)). The backend registers its media type, `application/manifest+json`, because Windows and slim images lack it (`gamma/app.py`; `tests/test_static.py`). |
| The icons | `frontend/public/media/icons/apple-touch-icon.png` (180), `icon-192.png`, `icon-512.png`, `icon-maskable-512.png` | Rendered from the one brand mark by `tools/branding/build.mjs` like every other icon ([design/brand](../../design/brand/README.md)); never edited by hand. The `bleed` option of `mark.mjs` gives them a full-bleed square plate — the OS masks the corners itself, and iOS paints transparent corners black — and the maskable one keeps the mark inside the inner 80%. |
| The head tags | `frontend/index.html` | `manifest`, `apple-touch-icon`, `apple-mobile-web-app-capable` / `mobile-web-app-capable`, the title, `apple-mobile-web-app-status-bar-style` **default**; the pre-paint script adds `theme-color` for the stored theme (below). |
| The status bar colour | `index.html`'s pre-paint script, then `app/App.jsx`, `paintStatusBar` (the theme effect and the phone-topbar effect) | With the *default* status-bar style the bar sits above the viewport and is painted with `theme-color`. Before the bundle loads, the pre-paint script writes the resolved theme's chrome colour from a small table of each theme's `--bg-page` (`tests/themes.test.mjs` holds it to the tokens), so a cold start never shows another theme's bar. From then on the meta is set to the topbar's background (the chrome, `--bg-page`) whenever the theme changes, so the bar continues the topbar for every theme — or to the library's (the content surface, `--bg-surface`) while the compact layout shows no topbar. Both are resolved to a hex first (`tokenHex`): most tokens are `color-mix()` expressions. `black-translucent` was rejected: it puts content under the bar with fixed light text, wrong on the light themes. |
| Full-screen CSS | `shared/styles/app.css`, `html[data-shell]` (set by `shared/lib/host.js` when the app runs installed or in the iPad app's shell) | `.app` pads `env(safe-area-inset-bottom)` for the home indicator. There is no top inset to absorb: with the default status bar the bar sits above the viewport, and the shell's web view starts below it. The document's `overscroll-behavior: none` (html/body; the panes still scroll) sits outside the block: a Safari tab gets it too. |

### Layout by orientation

An iPad's Safari sends a desktop-class UA, so the layout follows the
screen, not the device (`PHONE_MQ` / `useIsPhone` in App.jsx):

- **Upright** — a coarse pointer, portrait, at most 1024px wide (every iPad
  up to the 12.9" Pro, and Android tablets) — gets the compact shell the
  phone uses (`.phoneUI`): the PDF or the notes full width, Notes and Chat
  as full-screen panels, one bottom bar (below). Two docks in 820px would
  leave ~200px columns, too narrow for the chat header's buttons. At this
  width the bar's items are roomier and its popovers keep
  a menu's width at the right end instead of spanning the screen (the
  `min-width: 701px` block under the phone rules in `app.css`).
- **Landscape** gets the desktop docks, as on a laptop. Turning the tablet
  swaps the shell live; the dock arrangement is kept for the way back.
- A phone keeps the compact shell both ways: 700px wide or less, or a
  coarse pointer on a screen at most 500px tall.

#### The compact shell

- **The bottom bar** (`.phoneBottomBar`): view tabs on the left — Library
  (home: the topbar has no Home button here, so it stays on a page's bar
  and carries the `header.home` anchor), the page's PDF and Notes, Chat —
  and the topbar's actions on the right: Add, Search, a page's Share (and
  a clone's Sync pill), More. Every item is a 22px icon over its word
  (`.barLabel`, `--fs-2xs`, hidden on the desktop topbar), about 50px tall.
- **A page with sheets** ([notebooks.md](notebooks.md)) has Notebook and
  Notes tabs in place of the page's tab and the Notes panel's: they switch
  between its two views (`setNotebookView`), which close any panel over
  the page.
- **Panels and jumps.** Notes and Chat show as full-screen panels whose
  title is a plain label: no "⠿" grip, since a panel neither drags nor
  folds. A jump from the notes to the page (a handwriting or text box
  row's, a highlight's) closes the panel over it (`showOnPage`,
  `jumpToHighlightId`). Show note on the page brings the notes up
  (`showInNotes`): the Notes panel, or on a page with sheets its notes
  view.
- **More** is the account button (`header.account`) under another name:
  its sheet is the account menu with the rest of the topbar on top —
  Background tasks, the open folder's Share this folder, and the View
  menu's rows (`viewMenuItems`; of the window toggles only PDF, since the
  tabs switch Notes and Chat, then Undo, Redo, Go to page and Command
  palette). The tasks, folder-share and View buttons are
  not rendered; their popovers open from those rows in the sheet's place.
  A flyout (the account menu's Tours) opens inline under its row, since
  beside it the sheet's own scroll box would clip it. While something
  runs, More shows the tasks spinner.
- **The topbar** holds only tabs and Back, so with neither (a library with
  no tab open) it is not shown (`.topbar.phoneBare`), and theme-color
  switches to the library's `--bg-surface` so the status bar continues it
  (`paintStatusBar` in App.jsx).

### The editing bar

The notes take Tab, Shift+Enter and Ctrl chords for their structure and
formatting. The iPad's on-screen keyboard has no Tab or Ctrl, and no
dependable Shift+Return ([research/touch.md](../research/touch.md)). So
while a note's editor is open on a touch screen, a bar sits on top of the
keyboard (`editor/EditBar.jsx`):

**Insert** (the `/` menu) · Outdent · Indent · Move up · Move down · New
block · Bold · Italic · Inline code · Strikethrough · Link · Inline
equation · Next math argument · Undo · Redo … **Done**

The iPad app shows this same bar, since it is the web app.

- **Every tool is a command.** All but Undo and Redo are block commands
  ([hotkeys.md](hotkeys.md)), run with the context the row's keydown
  dispatches with (`commandContext` in `BlockTree.jsx`). So a button does
  what the same command does from a key or the palette.
  - Four of them exist for the bar and have no default chord:
    `block.insertMenu`, `block.newBelow`, `block.math` and
    `block.nextSlot` (each is described in [hotkeys.md](hotkeys.md)).
  - Undo and Redo are the page's block history (App's `undoBlocks`), as
    Ctrl+Z.
  - A button whose command's `when` fails is greyed out: Outdent at the
    top level, Indent and Move up on a first child, Move down on a last.
  - Next math argument is Tab's hop in a math snippet (the next `{}`
    slot, past a `\right`, out of the span), which an on-screen keyboard
    has no key for. It is the one transient tool (`TRANSIENT`): it shows
    only while its `when` holds, the caret in math with somewhere to go,
    instead of greying out.
  - Done blurs the editor, which closes it and the keyboard.
- **When it shows.** It shows when the primary pointer is coarse, or the
  last press was a finger or a pen (a touch laptop). Where the primary
  pointer is fine, a mouse or trackpad press never brings it. It is read
  once when the editor opens (`touchTyping()` in `shared/lib/pointer.js`,
  [ui-design.md](ui-design.md#one-behaviour-for-mouse-and-finger)). A
  read-only page opens no editor, so it has no bar.
- **What it looks like.** The markup tool strip's surface and separators
  (`.pdfInkBar`, `.pdfInkSep`), centred and floating 6 px above what it
  sits on. Its `ctlBtn`s carry the strip's 16 px icons and are 36 px, the
  ink menu's finger size.
- **Where it sits.** It is `position: fixed`, its `top` set from the
  visual viewport's bottom, the top of the keyboard (`measure`). iPadOS
  does not shrink the layout viewport for its keyboard, and `top` is
  measured in the layout viewport, as `visualViewport.offsetTop` is.
  - iPadOS moves the visual viewport itself: the keyboard slides, and a
    scroll pans the view under the keyboard. Safari draws that move at
    once, but a script can only follow a frame later, so a bar chasing it
    would shake. Instead the bar steps aside while its place keeps
    changing (`.moving`: invisible, not pressable). It fades back in once
    its place has been still for 150 ms (`useBarPlace`).
  - Opening an editor counts as a move for 350 ms, the keyboard's rise, so
    the bar appears with the keyboard rather than under it.
  - The events do not always come at the end of a move, and none come when
    the keyboard returns to an editor that kept the focus. So the
    viewport's and the window's events, a focus change and a tap each
    watch the place for a second, a frame at a time.
  - It never sits lower than the top of the compact shell's bottom bar.
    That bar must stay reachable when no keyboard is up: a new page opens
    its first block's editor by itself, and a hardware keyboard raises
    none.
  - With neither a keyboard nor a bottom bar, it sits on the screen's
    edge, clear of the home indicator (`.editBar.atEdge`).
  - Its layer, `--z-editbar`, is over the compact shell's full-screen Notes
    panel and under the editor's own popups.
  - The room it takes at the visual viewport's bottom (`editBarSpace()`)
    is the editor's bottom scroll margin, so the caret scrolls into view
    above it. Caret-anchored popups (the slash menu, the math preview,
    `useCaretAnchored`) leave that room free.
  - On a narrow screen the tools scroll sideways and Done stays at the end.
- **The keyboard stays up.** iPadOS keeps it while focus stays in the
  editor, and raises it again only for a `focus()` made during a gesture.
  - A press never takes focus: the bar cancels `pointerdown` and
    `mousedown`, and its buttons have `tabIndex` −1.
  - A command runs inside `flushSync`, so a row that re-renders, or
    remounts, does so within the tap.
  - Indent and outdent remount the row. App keeps its editor open through
    that (`keepEditorThrough`, [hotkeys.md](hotkeys.md#the-block-commands-plumbing)),
    and the bar puts the caret back in the same tap (`keepCaret`).
  - A Backspace in an empty block moves on to the end of the block shown
    above (`onDelete`'s `focusAbove`).
- **The keyboard's own undo.** The iPad keyboard's undo and redo keys, and
  the three-finger swipes, send `beforeinput` `historyUndo` /
  `historyRedo`. A note row's `BlockCmEditor` hands them to the block
  history (its `onUndo`), so the browser never undoes CodeMirror's DOM
  there. An editor without `onUndo` (a text box's, an embed card's) leaves
  them to the browser.
- **The keyboard's own formats.** The iPad keyboard's B and I, and the
  Format menu over a selection, send `beforeinput` `formatBold`,
  `formatItalic` and `formatStrikeThrough`. Every `BlockCmEditor` runs
  `block.bold`, `block.italic` and `block.strike` for them, the commands
  of Ctrl+B, Ctrl+I and Ctrl+Shift+X.
- **Typing aids.** CodeMirror switches the system's typing aids off. In
  prose the editor turns them back on (`autocorrect`, `autocapitalize`
  sentences, `spellcheck`, `writingsuggestions`); in a code fence or math,
  where a corrected word is a broken command, they stay off. The caret's
  place decides as it moves (`typingAids` in `BlockCmEditor.jsx`).

Apart from the bar and the compact shell, the touch rules are the ones the
ink layer and the viewers already carry, and the shared ones every control
follows ([ui-design.md](ui-design.md#one-behaviour-for-mouse-and-finger)).
The viewport meta asks for no browser zoom, leaving pinch-zoom to each
viewer (the PDF's and the notebook's, [notebooks.md](notebooks.md)).
Safari tabs ignore it, so on any touch screen, in either layout, the app
also refuses Safari's gesture events (`touchScreen()`, `App.jsx`). The
viewers' own pinch runs off `touchstart` / `touchmove` and is unaffected.
`touch-action: manipulation` removes double-tap zoom.
`overscroll-behavior: none` on html and body keeps the document from
rubber-banding or pulling to refresh, in a Safari tab as installed. Only
the panes scroll.

### Not built (and why)

- **No service worker or offline shell.** Installability on iOS does not
  need one, and a cache layer would sit on top of the asset cache rules
  in [repository.md](repository.md). Offline use is the iPad app's,
  through its own server ([The local server](#the-local-server)).
- **Pencil double tap and squeeze** are not exposed to web content by
  iPadOS. Pencil hover is exposed (a `pen` pointer with no buttons) and
  already shows the tool footprint. The iPad app forwards both
  ([The pen](#the-pen)).

### Tests

- `backend/tests/test_static.py`: the manifest's media type and cache
  header.
- `frontend/tests/editBar.test.mjs`: the Insert and Inline equation edits
  (`slashInsertAt`, `mathInsertAt` in `editor/markCommands.js`; neither
  applies inside math or a code fence), where Move up / down, Indent and
  Outdent apply (their `when`), Next math argument's hop and where it
  applies, and the bar's four commands starting unbound.
- `tests/e2e/scenarios/touch.mjs` (`npm run e2e -- --only touch`), by
  finger on an emulated tablet: Inline code and Strikethrough wrap a
  selection, Next math argument shows only in a math argument and hops to
  the next, the typing aids are on in prose and off in math, and a tapped
  picture's toolbar ⋯ opens the object menu.
- `tests/e2e/scenarios/ipad.mjs` (`npm run e2e -- --only ipad`), in
  Chromium touch contexts:
  - Tablet-sized: the manifest parses with `standalone` display, and every
    icon it and the `apple-touch-icon` link name is a real PNG.
    `theme-color` matches what sits under the status bar (the library with
    no tab open, the topbar once one is) and follows a theme change. The
    document's overscroll is off, the bundled stylesheet carries the
    full-screen rule (`html[data-shell]`, the home-indicator inset), and a
    browser tab gets no `data-shell`. Chromium cannot emulate
    `display-mode`: CDP accepts the feature, but `matchMedia` ignores it.
  - The iPad app's shell, with a stand-in host: [the native app's tests](#tests).
  - Upright (834×1194) is the compact shell: the PDF full width, Notes full
    screen from the bottom bar. Rotating to landscape brings the docks back.
  - A 390px phone: no topbar without tabs, the bar's words (Library Chat
    Add Search More, then Library Notes Chat Add Search Share More on a
    page), the listing bar inside the screen, a long title wrapping with its
    chips under it, More's sheet opening Background tasks, and the Library
    tab going home.
  - The editing bar, upright, by tap. A tap on a note opens it with the bar
    floating centred above the bottom bar, its buttons 36 px wide, Outdent
    greyed out at the top level.
    - A stand-in visual viewport plays the keyboard. The bar lands on it
      when its event comes before the move, stays on it when the view
      scrolls, and finds it back on the next tap when it returns with no
      event. Through a pan that goes on for 40 frames it never shows, and
      it shows where the pan stops.
    - Indent, Outdent and Move down change the saved tree, and after each
      the same block's editor still has the focus.
    - New block focuses a new empty block.
    - Bold wraps the selection; Undo and Redo take it back and put it
      back, and so do the keyboard's `historyUndo` / `historyRedo` input
      events.
    - Inline equation inserts `$x$`, its x typed over.
    - Insert opens the slash menu, and a tapped item applies.
    - Done closes the editor and the bar.
    - A mouse context's editor has no bar.
  - Only the device can check the Add to Home Screen flow, the status-bar
    paint and the home indicator. It is also needed for these editing-bar
    behaviors:
    - the bar riding on the on-screen keyboard (emulation has none)
    - the keyboard staying up through every button, Indent and Outdent
      included
    - the keyboard's own undo key sending `historyUndo`
- `node tools/branding/build.mjs --check` pins the icons to the mark.
