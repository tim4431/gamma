# Gamma for iPad

A native iPad app that keeps a copy of one workspace of a Gamma server on
the iPad, reads its PDFs and notebooks, writes on them with Apple Pencil,
and syncs with the server whenever it can reach it. Everything else Gamma
does (search, AI chat, sharing, settings) stays in the web app, which the
app opens in a web view.

The developer documentation for the whole design is
[docs/dev/ipad.md](../docs/dev/ipad.md). This file covers building and
running the app.

## How it is put together

The app has no server of its own and no second implementation of Gamma's
rules. Two layers do the work:

- **JavaScript core.** The web app's own modules run in JavaScriptCore:
  the ink codec and geometry (`frontend/src/ink/ink.js`), the notebook
  rules (`frontend/src/notebook/notebook.js`), the note editor's text
  commands (`frontend/src/editor/markCommands.js`, `slashInserts.js`), and
  the replica (`frontend/src/replica/`). The replica is the desktop
  mirror's sync protocol and merges, ported to run on a device. `core/entry.js` is the
  bundle's entry, and `scripts/build-core.mjs` bundles it into
  `GammaIPad/Resources/gamma-core.js`.
- **Swift host.** It stores, draws and talks to the network, and makes no
  decisions. The core calls one function, `invoke(method, argsJSON)`,
  which `Core/Replica.swift` answers from SQLite (`Core/Store.swift`), the
  files (`Core/FileStore.swift`) and HTTP (`Core/Remote.swift`).
  `Core/GammaCore.swift` runs two contexts: one for sync rounds and one
  for the editors, so drawing never waits for the network.

| Folder | What it holds |
|---|---|
| `GammaIPad/App` | The app entry and the model: connection, replica, sync cadence |
| `GammaIPad/Core` | The JavaScript bridge, the host, the store, files, HTTP, Keychain |
| `GammaIPad/Views` | Connect (web sign-in, workspace, token), library, page, notes and their editing bar, web view |
| `GammaIPad/Reader` | The PDF reader (PDFKit with ink overlays) and the notebook |
| `GammaIPad/Ink` | The ink session (groups, undo, save), the drawing view, the Pencil recognizer, the tool strip |
| `GammaIPadTests` | XCTest: store semantics, file names, the bundled core through the host |
| `core/`, `scripts/` | The bundle entry, its build, and its test in a bare JavaScript context |

## Build and run

You need a Mac with Xcode 16 or newer, Node 22 or newer, and
[XcodeGen](https://github.com/yonaskolb/XcodeGen).

```bash
cd frontend && npm ci && cd ..      # the web app's dependencies (esbuild, perfect-freehand, …)
node ipad/scripts/build-core.mjs    # bundle the JavaScript (the build also does this)
node --test ipad/scripts/core.test.mjs
cd ipad
xcodegen generate                   # makes GammaIPad.xcodeproj from project.yml
open GammaIPad.xcodeproj
```

Pick an iPad simulator or your iPad, set your development team under
Signing & Capabilities, and run. The project targets iPadOS 17 and iPad
only.

In the app, enter your server's address and sign in on its own page. Any
sign-in the server offers works. Then choose a workspace. The app mints a
write token for that workspace, the credential a desktop clone uses, and
keeps it in the Keychain. The first sync brings the workspace over,
including every PDF.

A server reached over plain `http` must be on the local network (App
Transport Security allows local networking only). Use `https` otherwise.

## Tests

- `node --test ipad/scripts/core.test.mjs` runs the bundle the way
  JavaScriptCore does, with no browser or Node globals.
- `xcodebuild test -scheme GammaIPad -destination 'platform=iOS Simulator,name=<an iPad>'`
  runs the XCTest suite.
- The sync rules are tested against a real server by the web app's
  browser suite, in group `replica` (`cd frontend && npm run e2e -- --group replica`).
  The pure rules also have shared fixtures that the Python engine passes
  too (`tests/shared/synctree.json`, `textmerge.json`, `inkmerge.json`).
- CI runs all of it on macOS: `.github/workflows/ipad.yml`.

## Not built yet

- Text selection makes no highlight. PDFKit selects text, and the
  highlight is made in the web view.
- The lasso, the partial eraser, and moving or restyling strokes exist in
  the web app only. The iPad has pens, highlighters, the whole-stroke
  eraser, undo and redo.
- Every PDF of the workspace is downloaded. A per-page "keep offline"
  choice is not built.
- Sync runs while the app is in front. Background refresh is not built.
