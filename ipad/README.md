# Gamma for iPad

The iPad app is a shell over Gamma's web app. It shows a server's own
interface in a web view, under the desktop app's own bar and launcher,
and adds what Safari cannot give that web app:
Apple Pencil strokes captured natively, the Pencil's double tap and
squeeze, a Gamma Cloud sign-in that works with Google, files shared to
it from other apps, and a Gamma server of its own inside the app ("This
iPad"), so it works with no connection.
Every page, tool and setting is the web app's, so a web feature reaches
the iPad with no work here.

The design, the host contract, the chrome, the local server and the
device checks are in [docs/dev/ipad.md](../docs/dev/ipad.md). The survey
that chose this shape is
[docs/research/ipad-web-shell.md](../docs/research/ipad-web-shell.md),
and the one that gave it the desktop's chrome is
[docs/research/ipad-desktop-chrome.md](../docs/research/ipad-desktop-chrome.md).
This file covers how the app is put together, building it, and its tests.

## How it is put together

The web app is the one owner of the page. Native code never holds a page,
a stroke, a tool or a token. It is a window, an input device, a preview
layer and a process manager, and nothing in Swift knows what a block is.
There are three parts:

- **The shell** (`Shell/`). One `WKWebView`, made once and kept, shows the
  chosen server's web app. A user script declares the host before the
  bundle runs, and the page and the app talk through a small message
  vocabulary (`Bridge.swift` here, `frontend/src/shared/lib/host.js`
  there, changed together). Other sites, downloads and the Gamma Cloud
  sign-in leave the web view for the system's own sheets. Above it, the
  desktop app's own bar and launcher (`desktop/ui/`, copied into the
  bundle) show in web views of their own and call the app through
  `gammaShell`, answered as the desktop's preload answers it
  ([docs/dev/ipad.md "The chrome"](../docs/dev/ipad.md#the-chrome)).
- **The Pencil layer** (`Pencil/`). A recognizer on the web view's
  container records every Pencil touch. The page's ink layer decides what
  a touch is and claims a stroke; the recognizer then takes the touch,
  draws the live line and hands the samples back, and the page saves them
  through its own path ([docs/dev/ipad.md "The pen"](../docs/dev/ipad.md#the-pen)).
- **The server on the device** (`Server/`, `python/`, `scripts/`).
  CPython 3.13 for iOS runs the backend from `backend/`, unchanged, on
  `127.0.0.1`, as the desktop app runs its sidecar, and serves the web app
  built from `frontend/`. Offline copies of another server's workspaces
  are Gamma's own clones, made in the web app
  ([docs/dev/ipad.md "The local server"](../docs/dev/ipad.md#the-local-server)).

| File | What it does |
|---|---|
| `App/GammaApp.swift` | The entry: the shell, full screen. It checks the local server again when the app is active again, and hands a file the system opens in the app to `OpenedFiles` |
| `App/AppModel.swift` | The server shown, "This iPad" or another server by address, the other servers and their names, in `UserDefaults`; opening one, a failed load or start sent to the launcher, and whether the launcher is up |
| `App/ShellState.swift` | The state the bar and the launcher read, built as the desktop builds its own, and the failures the launcher shows |
| `Server/LocalServer.swift` | The local server: the port, the data directory (`Documents/Gamma/local`, visible in Files), the seeded account, the environment, the wait for `/api/health`, and the restart after a failed start or a socket iOS reclaimed |
| `Server/PythonRuntime.swift` | The embedded CPython: configured and started once on a thread of its own, running `gamma_ios.main()`; `restart` reaches the supervisor in it through the GIL |
| `Server/Keychain.swift` | The seeded account's password in the Keychain |
| `Server/GammaIPad-Bridging-Header.h` | Python's C API for Swift |
| `Shell/ShellView.swift` | The window: the web view, the live stroke over it, the launcher while it is up and the bar on top unless it is hidden, the Pencil layer on their container; the chrome's calls answered, its state pushed, and the ⌘⇧L, ⌘R and ⌘⇧B shortcuts |
| `Shell/WebShell.swift` | The server's page's `WKWebView`, set up once: desktop content mode, no bounce, no inset adjustment, no keyboard accessory bar, inspectable in Debug builds, reloaded when the system ends its process; the silent sign-in to the local server, the session read for the bar's workspaces, and the theme's name |
| `Shell/Chrome.swift` | The desktop's bar and launcher in web views of their own: the bundle's `chrome` folder, the `gammaShell` user script and the handler it posts to, the state pushed to the pages, and a page loaded again when the system ends its process |
| `Shell/Bridge.swift` | The message vocabulary as Codable types, the handler the page posts to, and `send` back (`ask` when the shell needs the answer, as for `hold`). The only file that knows the web app's protocol |
| `Shell/Navigation.swift` | Other sites and `target=_blank` in a Safari view, downloads to the share sheet, the microphone for recordings, the Gamma Cloud start handed to `CloudSignIn` |
| `Shell/OpenedFiles.swift` | The files the system opens in the app (the share sheet, Files' Open in, AirDrop), copied into `tmp/Opened` and held until the page has read them through `files`, `fileData` and `fileDone` ([docs/dev/ipad.md "Opened files"](../docs/dev/ipad.md#opened-files)) |
| `Shell/CloudSignIn.swift` | The desktop app's Gamma Cloud sign-in (`app-start`, a loopback return, `app-claim`) through a system browser sheet |
| `Pencil/PencilCapture.swift` | The recognizer that takes a Pencil touch once the web claims it, the hold timer, and the pure parts it uses (claim matching, sampling with the tilt and the stillness anchor, when a hold is asked) |
| `Pencil/LiveStrokeView.swift` | The stroke being drawn, in the claim's look (a ribbon from the force, a calligraphy pen's ribbon from its travel against the nib, a dash, a multiplied highlighter, smoothing through the lazy brush), along the ruler as the line between the samples' outermost projections onto its edge (`RulerSnap`), or as the held shape, until the web has painted it |
| `Pencil/InkWidth.swift` | The brush table of the web's `ink/finishStroke.js`, ported: the width and opacity laws the ribbon is drawn with, held to `tests/shared/inkwidth.json` |
| `Pencil/HeldShape.swift` | A shape from the web's answer to `hold`: its outline, its refit as the pen moves, and the rule that drops it (`ShapeHold`), held to `tests/shared/inkhold.json`; also `mod`, the web's fold into [0, m), which the live nib and the samples' roll use too |
| `Pencil/RulerSnap.swift` | The ruler's edge a claimed stroke runs along (the claim's `snap`): every point projected onto the edge, the live line between the two outermost projections, as the web's `ink/ruler.js` commits it |
| `Pencil/PencilGestures.swift` | The double tap and the squeeze, with its phases, forwarded with the system's preferred action; where the Pencil hovered for each, for its tick |
| `Pencil/PencilHaptics.swift` | The web's `haptic`, each kind as Apple's feedback for its event (a held shape's path completion, a pen lasso's selection change, a gesture's light impact: `HapticFeel`), in an Apple Pencil Pro from iPadOS 17.5 |
| `python/gamma_ios.py` | The server's entry on the device: output to the log, then uvicorn on `127.0.0.1` at `GAMMA_PORT` under a supervisor the app can ask to run it again |
| `python/requirements-ios.txt` | The server's dependencies as the iPad installs them |
| `scripts/build-python.sh` | Prepares Python, the wheels, the server code and the web app for the bundle (below) |
| `project.yml` | The XcodeGen project, with the build phases that run the script, install Python into the bundle and copy in the desktop's chrome pages |

## Build and run

You need a Mac with Xcode 16 or newer,
[XcodeGen](https://github.com/yonaskolb/XcodeGen), Node.js (for the web
app) and a Python 3.13 on the `PATH` as `python3.13` (Homebrew's or
python.org's). From the repository's root:

```bash
bash ipad/scripts/build-python.sh   # once from a shell, before the first build
cd ipad
xcodegen generate                   # makes GammaIPad.xcodeproj from project.yml
open GammaIPad.xcodeproj
```

The script has to run once from a shell before the first build, because
Xcode reads `Python.xcframework` when it plans a build. After that, Xcode
runs it before each build, and it is quick when nothing changed. Call it
through `bash`: its executable bit does not survive a commit from a
Windows checkout.

Pick an iPad simulator or your iPad, set your development team under
Signing & Capabilities, and run. The bundle id, `net.blitzbuild.gamma`, is
the App Store record's, of team `A436A36DDC`: a device build signed by
another team needs a bundle id of its own there. The project targets
iPadOS 17 and iPad only. The simulator wheels are arm64, so the
simulator needs an Apple Silicon Mac. A build for more than one
architecture (a Release build for the simulator) needs
`ONLY_ACTIVE_ARCH=YES`, because `install_python` copies one
architecture's standard library.

In the app, the launcher shows at the first launch: open **This iPad**, or
add another server's address, open it and sign in on its own page. The
bar's mark, *All servers…* in its dropdown, or ⌘⇧L brings the launcher
back. The chevron at the bar's right end, or ⌘⇧B, hides the bar, and the
web app's View → *Server bar* brings it back. A server reached over plain
`http` must be on the local network, because App Transport Security
allows local networking only. Use `https` otherwise. In a Debug build,
Safari's Web Inspector on a Mac opens the page, and the bar and the
launcher too (Develop → your iPad).

### Python on the device

`scripts/build-python.sh` makes everything the server on the device
needs, all of it generated and gitignored (`ipad/.gitignore`):

| Output | What it is |
|---|---|
| `Python.xcframework/` | CPython 3.13.16 for iOS, BeeWare's [Python-Apple-support](https://github.com/beeware/Python-Apple-support) `3.13-b16`, downloaded once and checked against the sha256 pinned in the script, with OpenSSL's privacy manifest (`python/OpenSSL.xcprivacy`) put beside its `_ssl` and `_hashlib` modules on every run |
| `GammaIPad/Resources/python/app_packages.iphoneos/` | The server's dependencies as iOS device wheels |
| `GammaIPad/Resources/python/app_packages.iphonesimulator/` | The same as simulator wheels; a build takes the one for its platform |
| `GammaIPad/Resources/python/app/` | `backend/app.py`, `backend/gamma/` and `python/gamma_ios.py`, with bytecode compiled for the device |
| `GammaIPad/Resources/python/frontend_dist/` | The built web app the local server serves |

```bash
bash ipad/scripts/build-python.sh           # prepare everything (macOS)
bash ipad/scripts/build-python.sh --check   # only check that every pin resolves, for both platforms (macOS or Linux, no Xcode)
```

- **The cache.** The tarball, pip's cache and the pip the script runs
  live in `~/Library/Caches/gamma-ipad` (`GAMMA_IPAD_CACHE` moves it).
  The wheels are reinstalled only when `python/requirements-ios.txt` or
  the pip flags change, and the copies are rsyncs.
- **pip's Python.** pip runs from a venv the script makes in the cache,
  so the Mac's own Python is never touched. The venv is made from
  `python3.13` when there is one (`GAMMA_IPAD_PYTHON` names another),
  because pip evaluates environment markers for the Python it runs under.
  A venv made from another Python stays until it is deleted.
- **Bytecode.** The same Python compiles the standard library, the wheels
  and the server code, because the device writes no bytecode (its bundle
  is read-only) and would otherwise compile them all at each launch. That
  needs a 3.13; with another Python the script warns and ships source
  only.
- **The web app** is rebuilt through `tools/build_frontend_if_needed.mjs`
  when `frontend/` changed. That runs `npm run build`, which empties
  `frontend/dist` while it runs, so a dev server serving that folder on
  the same Mac shows errors meanwhile. The script runs `npm ci` first
  when `node_modules` is missing or older than the lockfile, and adds
  Homebrew's folders to the `PATH`, which Xcode's build scripts lack.
  Without Node, a copy built before is kept with a warning.

In the build, a post-build phase copies the server code, the web app and
the platform's wheels into the bundle, then runs the support package's
`install_python`, which adds the standard library and makes each native
module a signed framework; the two that link OpenSSL, `_ssl` and
`_hashlib`, get the privacy manifest Apple requires of them from the file
the script put beside each, and a build whose frameworks lack it fails
([docs/dev/ipad.md "How the bundle is made"](../docs/dev/ipad.md#how-the-bundle-is-made)).
PyMuPDF's two MuPDF libraries come as plain
dylibs, which `install_python` leaves where they are, so a step of our own
makes them frameworks the same way
([docs/research/pymupdf-ios.md](../docs/research/pymupdf-ios.md)).
Unsigned builds sign all of those ad hoc. The same phase also copies the
desktop app's chrome pages, `desktop/ui/` (the bar, the launcher, their
stylesheets and fonts), into the bundle's `chrome` folder, so a change to
them reaches the iPad at its next build.

`python/requirements-ios.txt` is `backend/requirements.txt` with the
changes an iOS wheel needs, each commented there; why each was made is in
[docs/dev/ipad.md "The wheels"](../docs/dev/ipad.md#the-wheels).

## Tests

- The XCTest suite, on a simulator:

  ```bash
  cd ipad
  xcodebuild test -project GammaIPad.xcodeproj -scheme GammaIPad \
    -destination 'platform=iOS Simulator,name=<an iPad>' ONLY_ACTIVE_ARCH=YES ARCHS=arm64
  ```

  It covers the pure parts (`BridgeTests`, `CloudSignInTests`,
  `PencilTests`, `InkWidthTests`, `ShellTests`, `ServerTests`,
  `OpenedFilesTests`, and `ChromeTests.swift` for the chrome's bridge,
  state and launcher rules; the ink
  fixtures they read, `tests/shared/inkwidth.json` and `inkhold.json`,
  are resources of the test bundle), and `LocalServerStartTests`
  starts the embedded server, waits for `/api/health`, then uploads a
  one-page PDF and reads it back through the server, its page picture
  included, which only PyMuPDF can draw: the bundle's MuPDF frameworks
  are proved there. It needs the Python step to have run.
- Without a Mac: `backend/tests/test_ios_requirements.py` (the iOS
  requirements keep up with the server's), unit tests for `host.js` and
  `transfers/openedFiles.js`, and the `ipad` browser group. The group
  fakes the host and checks that a claimed stroke is saved and that each
  kind of opened file goes where a picked one would
  (`cd frontend && npm run e2e -- --group ipad`).
- The bar and the launcher are the desktop's pages, which its own suite
  drives (`cd desktop && npm run e2e`).
- How the recognizer lives among WebKit's own recognizers, how the bar
  and the launcher behave under touch, and how the server fares on a real
  iPad, need a device. What each test covers and the list of device
  checks are in [docs/dev/ipad.md](../docs/dev/ipad.md#tests).
- CI prepares the bundle, generates the project, runs the tests and makes
  an unsigned device build on macOS: `.github/workflows/ipad.yml`
  ([docs/dev/github_actions.md](../docs/dev/github_actions.md#ipadyml)).
- TestFlight: a Mac mini signs and uploads a build for every commit on
  `main` whose message carries a new `ipad-test-release-NNN` marker; every
  desktop release from `main` pushes one
  ([docs/dev/github_actions.md](../docs/dev/github_actions.md#the-ipad-testflight-release)).

What is not built yet (background running, a store release) is listed in
[docs/dev/ipad.md](../docs/dev/ipad.md#not-built-yet).
