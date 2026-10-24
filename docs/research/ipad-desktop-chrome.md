# The iPad app with the desktop's chrome

Survey from 2026-10-23. Findings and a plan; nothing here is built. The
question: can the iPad app have the same shell as the Windows desktop app,
the bar with the mark, the "server · workspace" switcher and its dropdown
(the open server's workspaces, then every server with its dot, then *All
servers…*), and the launcher behind it, in place of the SwiftUI server
picker it has (`ipad/GammaIPad/Views/ServersView.swift`)? The method was a
source audit of `desktop/ui/`, `desktop/main.js`, `desktop/preload.js`,
`ipad/GammaIPad/`, `frontend/src/shared/lib/host.js` and the account menu
in `frontend/src/app/App.jsx`. Nothing was run on a device; what only a
device can show is marked **device check**. What the two shells do today is
in [dev/desktop.md](../dev/desktop.md),
[desktop/docs/architecture.md](../../desktop/docs/architecture.md) and
[dev/ipad.md](../dev/ipad.md).

## The short version

- **Yes, and the cheapest way is to show the desktop's own pages.** The
  desktop's chrome is two plain HTML files with no build step,
  `desktop/ui/bar.html` and `desktop/ui/launcher.html`, styled from a
  committed copy of Gamma's tokens and talking to Electron through one
  object, `gammaShell` (28 methods; the bar calls 12 of them). A second
  `WKWebView` in the iPad app can load those same files from the bundle,
  and a Swift object can answer the same calls. The look is identical by
  construction, and a change to the desktop's chrome reaches the iPad at
  its next build, as a web feature does today.
- **The iPad already has most of the state the bar renders**, only in
  SwiftUI: the servers and the one shown (`AppModel`), the reachability
  dots (`Health.probe`, cached 30 s as the desktop caches 20 s), the local
  server's state, address and data size (`LocalServer`). It lacks two
  things the desktop has: the open server's workspace list, which the
  desktop reads from `GET /api/session` through the content view's cookie
  session, and a name per server (the iPad lists remotes by address).
- **Two things are desktop-only and stay hidden**: the sync button (clones
  and folders on disk, of every running local server) and the update pill
  (electron-updater). The iPad has clones, made in the web app, but no
  folders on disk and no updater of its own; both pages already render
  from state and hide what the state does not have.
- **Four phases**, each shippable: the desktop's pages made host-neutral;
  the bar over the web view; the launcher in place of the SwiftUI picker;
  the clone chips and the sync panel, which are optional.

## What the desktop shows, and what the iPad has today

| Piece | Desktop (`desktop/`) | iPad today (`ipad/`) |
|---|---|---|
| Where servers are switched | The bar's switcher (`#wsBtn`, "server · workspace") and its dropdown; the launcher page for everything else | A SwiftUI `Form` (`ServersView`): full screen at first launch, a sheet when the web app's *Server…* row posts `servers` |
| Workspaces | In the dropdown, from `/api/session` read through the content view's session on every navigation and on each menu open; a pick loads `<origin>/?ws=<id>` | Only in the web app's own account menu |
| Servers | Named, `local` or `remote`, a running or reachability dot, the local one's address, a check on the current | *This iPad* with a state dot, address and data size; remotes by address with a reachability dot; edit and remove on a swipe |
| Theme | The preload mirrors the page's `data-theme`; the bar, the launcher and the title bar repaint from Gamma's tokens | The page's `theme` message (a hex and a scheme) paints the strip under the status bar |
| Starting, failing | "Starting X…" in the bar's status; the launcher's failure panel with the cause, the matched line, a button that fixes it, and the log folded away | `LocalStartView` over the shell while *This iPad* starts; a `ContentUnavailableView` with Try again and Choose a server when a server cannot be reached |
| Keyboard | Ctrl/Cmd+Shift+L the launcher, Ctrl+R reload | Nothing |
| Chrome before the page | Always: the bar is the window's title bar | None: the web app's topbar, once it has loaded |
| Clones | *clone* / *open clone* / *origin* chips on the workspace rows; the sync button's panel | Made and shown in the web app's Settings; the shell knows nothing of them |

## Three ways to the same look

1. **Rebuild the bar in SwiftUI with Gamma's colours.** Every token of
   every theme ported by hand (seven themes, the recipes `tokens.css`
   derives from a scheme, Inter, the 1.6 px stroke icons), and a copy that
   drifts with each change to the desktop's chrome. The research note that
   shaped the app rejected exactly this kind of second implementation
   ([ipad-web-shell.md](ipad-web-shell.md), "The precedents"). Rejected.
2. **Render the bar inside the web app**, under `data-host="ipad"`, with
   the server list supplied by the host through new messages. One web
   view, the theme and the translations for free. But there is no bar
   before the page has loaded (the sign-in page, an unreachable server, the
   local server's first start, which can take minutes), an older server's
   web app has no bar at all, and the desktop's bar would exist twice,
   once in React and once in HTML, which is the drift of option 1 in
   another place. It also inverts the desktop's rule that the shell
   patches nothing in the frontend. Rejected.
3. **A second `WKWebView` showing `desktop/ui/bar.html`, and
   `launcher.html` in the content view**, as the desktop's `BaseWindow`
   holds two `WebContentsView`s. The pages are already host-aware
   (`body[data-platform]`), already render from one state object, and are
   already driven by the desktop's end-to-end suite through stable
   selectors (`#wsBtn`, `#menu .item[data-id]`, `#menu .wsItem[data-ws]`),
   which an iPad UI test can reuse. **Picked.**

## How it fits the iPad's shape

The app's rule stands: the web app owns the page, and the Swift side holds
no page, stroke, tool or token ([dev/ipad.md](../dev/ipad.md), "How it is
put together"). The chrome adds a second page the shell owns, as the
desktop owns its bar, and nothing about it knows what a block is.

- **Two web views.** `ShellController.loadView` gets a chrome view above
  the content: a `WKWebView` loading `bar.html` with
  `loadFileURL(_:allowingReadAccessTo:)` from the bundle's chrome folder,
  `isOpaque = false` with clear backgrounds (the page paints only the
  strip and an open menu), scrolling off, pinned to the safe area's top
  and 38 pt tall (or the touch height below). The content web view and the
  live stroke view start at its bottom instead of the safe area's top, so
  one CSS pixel stays one point and the pen's places still line up. The
  Pencil capture stays on the container and sees both views; a claim only
  ever comes from the server's page, so a Pencil on the bar is an ordinary
  tap.
- **The menu over the content.** The desktop enlarges the bar view over
  the content while a dropdown is open (`gammaShell.barExpand`), since a
  38 px view cannot show a menu. The chrome view does the same: its
  bottom constraint moves to the container's bottom while a menu is open
  and back when it closes. A tap on the see-through area closes it; the
  page does that itself.
- **The bridge.** A `WKScriptMessageHandlerWithReply` named `shell`,
  installed in the chrome view's configuration (and in the launcher
  view's), answering only a main frame whose URL is a `file:` URL inside
  the bundle, as the desktop's preload exposes `gammaShell` only on
  `file:` pages and nothing on a server's. The content view never gets it. A user script at document
  start defines `window.gammaShell` as `{platform: "ipad", version, ...}`
  with each method a thin wrapper that posts `{method, args}` and returns
  the reply's promise, and `onState(cb)` keeping the callback that
  `window.gammaShell._push(state)` calls, which Swift reaches through
  `callAsyncJavaScript` whenever the state changes. A method the iPad does
  not implement rejects, and the pages hide its control from the state (the
  `can` flags below), so the files stay one set.
- **The state** is a Codable mirror of the desktop's `barState()` and
  `fullState()` (`desktop/main.js`), and where each field comes from:

  | Field | Desktop | iPad |
  |---|---|---|
  | `platform`, `version` | `process.platform`, the app's version | `"ipad"`, `CFBundleShortVersionString` |
  | `theme` | the page's `data-theme`, mirrored by the preload | the theme's name, added to the web's `theme` message (below), kept across launches as the background is |
  | `current` `{id, name, type}` | the registry's open server | `AppModel.current`: `local` → `{id: "local", name: "This iPad", type: "local"}`, a remote → its saved entry |
  | `busy`, `notice` | "Starting X…", a sign-in's progress | `LocalServer.state` starting (and `slowStart`), `CloudSignIn.report`'s message |
  | `servers[]` `{id, name, type, url, running, reachable}` | the registry, `sidecar.status`, the health cache | `SavedServers` with names and ids (below), `LocalServer.url` and `.state`, `AppModel.reachable` |
  | `gamma` `{user, current, list}` | `/api/session` through the content session | the same request, run as a `fetch` in the `.defaultClient` content world the silent sign-in already uses (`WebShell.autoLoginScript`): same origin, the cookie goes along, nothing is injected into the page's world. Read when a main-frame load finishes (`Navigation.onLoad`) and when `state()` is asked with a menu opening. `current` is the `ws` query parameter of `webView.url`, else the reply's `default_workspace` |
  | `keeping`, `update` | the sync summary, electron-updater | `{summary: {state: "none"}, items: []}` and `{state: "none"}` until phase 4 |
  | `can` (new) `{addLocal, updates, storage, reveal, keeping}` | all true | `addLocal` false (one *This iPad*), `updates` false, `storage` false, `reveal` true (Files), `keeping` false until phase 4 |
  | the launcher's extras: `dataDir`, `sizeBytes`, `logPath`, `settings`, `lastOpened` | the registry, `dirSize`, `userData/logs` | `Documents/Gamma/local` shown as the Files path, `LocalServer.dataSize()`, `logs/server.log`; `settings.openLast` is new (the app reopens the last server unconditionally today) |

- **Server ids and names.** `SavedServers` keeps `current` and `remotes:
  [URL]`. The bar wants an id and a name per row, and the launcher renames.
  `remotes` becomes `[{id: UUID, name, url}]`, read from the old shape the
  first time with `name = host` and a fresh id, as the earlier migration
  from `gamma.server` did (`SavedServers.load`).
- **Opening.** `open(id)` is `AppModel.open`; `openWorkspace(id)` loads
  `<origin>/?ws=<id>` into the content view, what the desktop's
  `shell:open-workspace` does and what the web app's own switcher does
  (`switchWorkspace`); `reload` is `webView.reload()`; `launcher()` shows
  `launcher.html` in a web view of its own over the page (phase 3, below).
- **The theme's name.** The desktop learns it from `data-theme`; the iPad's
  `theme` message carries a hex and a scheme. The message gains `theme`,
  the name (`host.js`, the `theme` effect in `App.jsx`, `Bridge.swift`,
  the table in [dev/ipad.md](../dev/ipad.md#the-host-contract)). An older
  web app sends none, and the bar paints from the scheme (`paintTheme`
  already falls back). With a bar, the strip under the status bar is the
  bar's own `--bg-page`, as the desktop's title bar is: the bar page posts
  the colour it painted, and the container takes it, while the web's `bg`
  still goes to `underPageBackgroundColor`.
- **The `servers` flag goes.** `HostInfo` stops declaring `servers`, so the
  web app's *Server…* row and the sign-in page's link disappear on their
  own (`nativeHost()?.servers`), as the desktop's content has neither; the
  web code stays for older shells. A `servers` message from an older page
  still opens the launcher. No new flag is needed.
- **The launcher over the page, not in it.** The desktop loads
  `launcher.html` into its content view, so the server's page unloads and
  is loaded again when a server is picked. On the iPad that reload costs
  more than on a PC (the web app's cold start, pdf.js rendering the page
  again), and today's picker is a sheet over the live page, which a cancel
  leaves untouched. So the launcher gets a `WKWebView` of its own, made
  when asked and dropped when closed, between the content view and the
  bar: the page beneath stays alive, and the bar stays above it. Under
  `data-platform="ipad"` the launcher page shows a *Done* button while a
  server is open beneath (the desktop needs none: one picks a server). The
  content view keeps loading server pages only, as the desktop's
  navigation allowlist has it, and `Route` does not change.
  `allowingReadAccessTo` names only the chrome folder.
- **Keyboard.** `UIKeyCommand`s on the controller: ⌘⇧L the launcher, ⌘R
  reload; the bar's hint reads "⌘⇧L" under `data-platform="ipad"`.

## The plan

### Phase 1: the desktop's pages ready for a second host

In `desktop/ui/` and the iPad's build, no behaviour change on the desktop.

1. `bar.html`: `body[data-platform="ipad"]` rules. No 146 px right reserve
   and no 80 px left inset (both are for window controls the iPad has
   none of); the shortcut hint; `.menu` gets a `max-height` with
   `overflow-y: auto` (a landscape 11" iPad is 834 pt tall under the bar,
   and a long server list must scroll); and the touch sizes, under that
   rule only: the bar 44 pt, `.item` rows about 36 pt (`padding: 9px`).
   This is the one place the two shells would differ by design; the
   desktop's 38 px and 30 px rows are mouse sizes, and Apple's 44 pt
   target is the reason. Keeping them identical is a choice to make
   explicitly, and the rule is three lines either way.
2. The `can` flags. `launcher.html` hides *New local server*, the
   *Updates* row, the *Local server storage* row and the two *Reveal*
   buttons when the flag says so; `bar.html` hides the sync button when
   `can.keeping` is false (today it shows once a server is open even with
   nothing kept). The desktop sets every flag true in `barState()`. Under
   `data-platform="ipad"`, `launcher.html` also shows a *Done* button
   while `state.current` is set (the launcher sits over a live page there).
3. The copy. `ipad/scripts/build-python.sh` rsyncs `desktop/ui/` (the two
   pages, `theme.css`, `tokens.css`, `fonts/`) into
   `GammaIPad/Resources/chrome/`, gitignored like `Resources/python`, and
   `project.yml`'s post-build phase rsyncs it into the bundle beside
   `python/`. No third copy of the tokens: `frontend/tests/themes.test.mjs`
   keeps checking `desktop/ui`, and the iPad takes what it checked.
4. `npm run e2e` in `desktop/` stays green.

Size: a day. Files: `desktop/ui/bar.html`, `desktop/ui/launcher.html`,
`desktop/main.js` (`can`), `ipad/scripts/build-python.sh`,
`ipad/project.yml`, `ipad/.gitignore`.

### Phase 2: the bar

1. `Shell/ChromeView.swift` (new): the chrome web view, its configuration
   with the `shell` handler, the expand and collapse, the painted colour.
2. `Shell/ShellBridge.swift` (new): the `gammaShell` user script, the
   handler with its `file:`-only check, decoding `{method, args}` into an
   enum (as `Bridge.swift` decodes `WebMessage`), and `push(state)`. The
   only file that knows this second protocol.
3. `App/ShellState.swift` (new): the Codable state, built from `AppModel`,
   `LocalServer` and the session read.
4. `AppModel.swift`: ids and names (the `SavedServers` migration), the
   session read and `gamma`, `openWorkspace`, `busy` from the local
   server's state, and `@Published` changes feeding `push`.
5. `ShellView.swift`: the layout with the bar; the `theme` answer also
   keeps the theme's name. `Bridge.swift`, `host.js`, `App.jsx`: the name
   in the `theme` message; `HostInfo` without `servers`.
6. `GammaApp.swift`: the bar is always up. Until phase 3, *All servers…*
   and a `servers` message open `ServersView` as the sheet they open today.
7. `Navigation.swift`: `onLoad` also triggers the session read.
8. The key commands.

Tests. `ShellTests`: the bridge refuses a server-origin frame and answers a
bundle `file:` frame; a workspace id from a URL with and without `ws`; the
`SavedServers` migration gives every remote an id and its host as name;
`ShellState` encodes to the desktop's shape, held to a shared fixture
`tests/shared/shell-state.json` that `desktop/test/` asserts `barState()`
against too (the pattern of `tests/shared/inkwidth.json`). In the
simulator, the CI start test gains a step: `evaluateJavaScript` clicks
`#wsBtn` in the chrome view and asserts `#menu .item[data-id="local"]`
exists, with the desktop suite's selectors. **Device checks:** the pen's
places line up with the content view moved down; a Pencil tap on the bar
opens the menu; the menu over the content takes touches while open and
the page gets them again after; the bar in both orientations and in Split
View.

Size: two to three days.

### Phase 3: the launcher

1. `launcher()` shows `launcher.html` in the launcher view over the page
   (above); *Done*, a pick and the bar's switcher close it. The first
   launch, with no server chosen, shows it over an empty content view, as
   the desktop does, and `settings.openLast` off starts there every time.
2. `fullState()` on the iPad (the table above). `revealData` opens
   `Documents/Gamma/local` in Files through a `shareddocuments://` URL;
   `revealLog` shares the log through the share sheet; `copyAddress` is
   `UIPasteboard`; `addRemote`, `rename`, `setUrl`, `remove` are
   `AppModel`'s, with `ServerAddress.parse` behind the two that take an
   address.
3. Failures. A local start that failed (`LocalServer.state`'s message and
   `logTail()`) and a remote load that failed (`Navigation.onLoad`'s
   message) become the launcher's `fail` with *Try again*, and the bar's
   `busy` reads "Starting This iPad…" while the server starts, with the
   slow-start sentence as its `notice`. The desktop's `lib/startup.js`
   rule table is Node; the iPad sends the plain message and the log,
   which is the desktop's own fallback.
4. `ServersView.swift` and `LocalStartView` are deleted, and the
   `ContentUnavailableView` in `ShellView` with them: one picker, one
   failure page. [dev/ipad.md](../dev/ipad.md) "The server picker" and
   "What the user does" are rewritten; `ipad/README.md`'s file table too.

Tests: the launcher's `fail` from each failure kind; `openLast`; a
simulator step that opens the launcher from the menu and reads its cards.
**Device check:** the Files deep link on iPadOS 17.

Size: two to three days.

### Phase 4: clones in the menu (optional)

The desktop annotates each remote workspace row with its clone on a local
server and offers *clone* on hover, which mints a write token on the
remote, signs into the local server and creates the mirror there, all over
the public API with the content session's cookies
([desktop/docs/architecture.md](../../desktop/docs/architecture.md),
"Clones"); the sync button sums up the clones and folders every running
local server keeps. The iPad's half: read *This iPad*'s workspaces and
`GET /api/mirrors` with the seeded account to build the same map, port
`keepOffline` (three requests), set `can.keeping` and fill `keeping` with
the clones only (the iPad keeps no folders on disk, and
`GAMMA_FOLDER_LINKS` is not set there). Worth doing once phases 2 and 3
are in use; until then the web app's Settings make clones, as today.

Size: two days.

## Risks and open questions

- **Mouse sizes on a tablet.** The desktop's bar and rows are below
  Apple's 44 pt; phase 1 step 1 proposes touch sizes under the iPad rule.
  Whether identical pixels or touch sizes win is a decision to take before
  phase 1.
- **A second web content process** for the chrome page, tens of
  megabytes, and a third while the launcher is open. The pen path is not
  in either: the Pencil capture and the live line are native views on the
  container, the stroke's commit is the page's, and pdf.js renders in the
  content process as before, so neither drawing nor rendering changes. The
  one plausible cost is memory pressure on an iPad with 4 GB and a large
  PDF at high zoom, where WebKit ends the content process sooner (the
  shell already reloads the page then, `Navigation.onTerminate`).
  **Device check** with Xcode's memory gauge before and after, on the
  pdf-zoom scenario.
- **The chrome view enlarged over the content** must not swallow a stroke
  in progress: a menu opens on a tap, and a tap ends a claim, so no stroke
  is live when it expands. **Device check** all the same.
- **A `file:` page and `fetch`.** The bar fetches nothing and the launcher
  fetches nothing; every request goes through the bridge. The session
  read runs in the content view's world, not in the chrome page.
- **The web's `theme` message under the compact shell** sends the
  library's surface colour while there is no topbar, meant for the status
  bar. With a bar the strip is the bar's colour and that value only paints
  under the page; nothing in the web app changes for it.
- **App Review.** Local HTML in the bundle talking to the app through a
  message handler is ordinary; no new entitlement.
- **The desktop's chrome gains a second consumer.** A change to
  `bar.html` or `launcher.html` must keep the `can` flags and the platform
  rule in mind. The shared state fixture is what keeps the two honest.

## Sources

`desktop/ui/bar.html`, `desktop/ui/launcher.html`, `desktop/ui/theme.css`,
`desktop/main.js` (`barState`, `fullState`, `pushState`),
`desktop/preload.js`, `desktop/docs/architecture.md`, `desktop/test/e2e.js`;
`ipad/GammaIPad/App/GammaApp.swift`, `App/AppModel.swift`,
`Views/ServersView.swift`, `Shell/ShellView.swift`, `Shell/WebShell.swift`,
`Shell/Navigation.swift`, `Shell/Bridge.swift`, `ipad/project.yml`,
`ipad/scripts/build-python.sh`; `frontend/src/shared/lib/host.js`, the
account menu in `frontend/src/app/App.jsx`, `frontend/tools/copy-tokens.mjs`,
`frontend/tests/themes.test.mjs`; [dev/ipad.md](../dev/ipad.md),
[dev/desktop.md](../dev/desktop.md), [ipad-web-shell.md](ipad-web-shell.md).
