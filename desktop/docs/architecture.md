# Desktop shell architecture

The shell is an Electron window plus a process manager. Gamma stays a black
box: the window shows whatever Gamma server is open, the way a browser tab
would.

## Servers and workspaces

Two levels, one switcher. A **server** is a Gamma the shell can open; a
**workspace** is one of Gamma's own libraries inside it (personal or shared,
with members and roles — [docs/dev/workspaces.md](../../docs/dev/workspaces.md)).
Before Gamma had workspaces the shell called its servers "workspaces" and
separate libraries meant separate local servers; now one local server holds
as many workspaces as you like, and the shell-bar menu switches both.

Servers come in two kinds:

- **Local** — a data directory under the shell's userData dir, served by a
  Gamma backend the shell spawns on `127.0.0.1:<free port>` with
  `GAMMA_DATA_DIR` pointed at it. Fully self-initializing: the server seeds
  schema + first admin on an empty data dir (and upgrades an older data
  layout at startup — [docs/dev/migrations.md](../../docs/dev/migrations.md)).
  The shell passes `GAMMA_ADMIN_USER`/`GAMMA_ADMIN_PASSWORD` on the first
  spawn (the server's one-time password print would be lost in the hidden
  console), remembers the credentials in its registry, and auto-logs-in
  after navigation by POSTing `/api/login` from the page. Several local
  servers are still possible (one data dir and process each; a sidecar keeps
  running until the app quits, so switching back is instant), but the usual
  shape is one local server with several workspaces.
- **Remote** — just a URL (e.g. the NAS Docker deployment). Normal login;
  the session cookie persists in the Electron profile per origin.

Servers are fully independent; there is **no synchronization** (move data
between them with Gamma's own per-workspace *Export* / *Import*).

A remote server is its address. One typed without a scheme gets `http://`
when it names the local network — `localhost`, a private range, a
`.local`/`.lan`/`.home`/`.internal` name, or an explicit port, the
self-hosted Gamma's shape — and `https://` otherwise (`registry.withScheme`).
The pencil on a remote's card edits the address as well as the name
(`shell:set-url` → `registry.setRemoteUrl`; the mirrors keyed by the old
origin follow), and when an `https://` address is refused — a self-signed
certificate, or a plain-HTTP server answering https — the launcher's
failure card offers *Use http:// instead* (`lib/startup.js`, action `http`).

### Gamma's workspaces in the switcher

While a server is open the main process reads `GET /api/session` through the
content view's cookie session (`session.fetch` with credentials — a public
API call, nothing injected into the page) whenever the content view
navigates, and again when the bar menu opens. The reply's `workspaces` list
and the `ws` query parameter of the current URL become `gamma` in the shell
state (`null` while signed out). The bar menu lists them above the servers;
choosing one navigates the content view to `<origin>/?ws=<id>`
(`shell:open-workspace`) — the same thing Gamma's own account-menu switcher
does. The bar button reads "server · workspace".

### Remote reachability

Local servers show a running dot (is the sidecar up). Remote ones show the
same dot from a **health probe**: the main process fetches `<url>/api/health`
(public, no session needed; 5 s timeout) per remote server, caches the
answer for 20 s, and exposes it as `reachable`
(`true` / `false` / `null` while unknown) in the shell state. Probes run on
demand — every launcher refresh and every bar-menu open ask for the list,
which triggers stale probes — and their results arrive asynchronously
through `pushState`, so the dot fills in a moment after the page renders.
Opening a remote server also records the outcome (success → reachable,
failure → unreachable). Green = reachable, red = unreachable, dim = not
probed yet.

### Clones

Any workspace of a remote server can be kept as a **mirror** on a local
server — a *clone* of its *origin*, in the UI's git vocabulary: in the bar
menu every such row shows a trailing *clone* chip on hover (`keepOffline`
in `main.js`, `shell:keep-offline`). Everything goes
through Gamma's public API with the content session's cookies — nothing is
injected into any page: a write-scope integration token is minted on the
remote for that workspace (`POST /api/integrations/tokens`), the first
local server is started (made, when there is none) and signed into with its
seeded admin credentials (`POST /api/login` through `session.fetch`, so the
cookie lands in the same profile the content view uses), the mirror is
created there (`POST /api/mirrors`) and the window moves to it. A failure
on the way deletes the token again; a copy the local server already holds
is opened instead of a second one. From then on the local server syncs on
its own ([docs/dev/mirror.md](../../docs/dev/mirror.md)). The shell keeps
no sync state and no token — only a **map** of copies in the registry
(`mirrors`: local server + workspace ↔ remote origin + workspace, written
by `keepOffline` and replaced from the local server's own `GET /api/mirrors`
whenever that server is open, so copies made or stopped from Gamma's
Settings show up too). The map gives the switcher its cross-links (a remote
row with a clone shows *open clone* and opens it, `shell:open-copy`; a
clone's row on the local server reads *clone* — `mirror_of` on the
session's workspace list — and its *origin* chip opens the workspace it
follows on the registered remote, `shell:open-original`) and tells the
shell which local servers to start at launch (`startBackgroundHosts`): a copy
syncs only while its server runs, so those run for as long as the app does,
whichever server the window shows.

### Folders on this computer

A folder of any server's workspace can be kept as a directory anywhere on
this computer: each paper's PDF beside a Markdown note of its highlights
and notes, kept up to date by a local server. Only the app's own local
servers keep folders on disk ([docs/dev/folder_sync.md](../../docs/dev/folder_sync.md)
"Folders kept by the desktop app"); a NAS only answers the reads. The
shell adds the native directory picker, the token, and two ways in.

**A folder's menu in Gamma's page.** In the desktop app a library
folder's right-click menu has *Keep on this computer…*
(`frontend/src/app/App.jsx`, shown when `IS_DESKTOP`). The page posts
`{source: "gamma-app", type: "keep-folder-on-disk", ws, folder}` to its
own origin, the way it greets the Connector extension. The page preload
passes that on (`shell:keep-folder-from-page`) and exposes nothing. The
shell takes it only from the content view's page of the open server, with
plain ids, and only when no other action runs. Then it runs `keepFolder`,
whose native picker is the user's say. A folder already kept opens its
directory instead.

**The sync panel's chooser.** The panel under the bar's sync button
(below, "Kept on this computer") ends with *Keep a folder of <workspace>
on this computer…*. It opens the panel's second level, listing the open
workspace's folders (`shell:folders` → `listFolders`: the open server's
`GET /api/sync/folders` with the content session's cookies, and the links
from `listLinks`). A folder already kept reads *on disk* and opens its
directory on click (`shell:open-path`, only for a directory one of those
links names). Any other asks for a directory on click (`shell:keep-folder`
→ `keepFolder`). An empty directory is used as it is; any other gets a
subdirectory named after the folder. Syncing and stopping a kept folder
are the panel's rows, below.

Under the test harness `GAMMA_SHELL_PICK_DIR` answers the picker, the
questions take their first answer, and a directory "opened" is only
recorded (`openDirectory`, like `openExternal`).

Where the link lives depends on the server:

- On a **local** server it is that server's own link, made with `POST
  /api/folder-links` and the full path. The sidecar keeps links because
  the shell starts it with `GAMMA_FOLDER_LINKS=1`. The server reads the
  workspace in-process.
- On a **remote** server there is no clone. The shell mints a read-scope
  integration token on the remote for that workspace (`POST
  /api/integrations/tokens`, a year, named after the folder). It makes the
  link on the **host**, the first local server, which `ensureHost` makes,
  starts and signs into when needed (the clones' host too). The link names
  the remote's origin, the workspace, the token and its id, and the host
  reads the folder over HTTP with the client's own source. Only that
  folder's files come down.

The links of a remote workspace are the host's links with that origin
and workspace. *Stop* deletes the link on the host and revokes the token
on the remote (`revokeToken`, with the window's session, which is signed
into the remote). `settings.folderHost` remembers the host so it starts
at launch like the clones' hosts (`startBackgroundHosts`). *Stop* on the
host's last remote folder clears it.

The shell keeps nothing else about links; the rounds and the status are
the server's, the host's for a remote folder. The bar reports each action
in its status line for a few seconds (`notice` in the shell state). The
first time a clone or a folder on disk is made, the shell offers
background mode (below), since both sync only while Gamma runs.

### Kept on this computer

The **sync button** at the bar's right end sums up everything this
computer keeps of Gamma: the clones and the folders on disk, of every
running local server, whichever server the window shows. It reads
*Synced*, *Syncing…*, *Conflicts*, *n problems* and the like, with a
matching icon and colour, and shows once a server is open or anything is
kept. Its **panel** lists the clones, then the folders on disk, each a row
with its name over its state and where it is, and a state dot:

- A click opens it: a clone in the window (`openServer`, then
  `openGammaWorkspace`), a folder's directory (`openDirectory`).
- *sync* runs a round now. For a clone that is `POST
  /api/mirrors/<ws>/sync` on its local server. For a folder it is
  `syncFolder`, which asks first whether to replace files changed on disk
  when there are any, and reports the round in the bar.
- *stop* (a folder) is `dropFolder`, which asks whether to keep the files
  or take back what the sync wrote.

The panel ends with the chooser above. Conflicts, detaching and a clone's
cadence stay in Gamma's own sync pill, in the clone.

`refreshKeeping` reads every running local server's `GET /api/mirrors` and
`GET /api/folder-links` every 15 s, every 2 s while something syncs, when
the panel opens and after each action. `lib/keeping.js` turns the rows into
items and the button's summary, with no Electron in it
(`test/keeping.test.js`, `npm test`). A clone reads as Gamma's sync pill
reads it (`mirrorState` in `frontend/src/collaboration/MirrorPopover.jsx`):
the same states in the same order, so the two never disagree. Change both
together. The result is the shell state's `keeping` (`{items, summary}`).
The panel's rows act through `shell:keeping-action` (kind, server, id,
action), so they need no open server.

Each local server is read in a cookie jar of its own
(`session.fromPartition('keeping-<id>')`, in memory, signed in once per
run by `signIn`). Every sidecar is `127.0.0.1` and a cookie does not tell
ports apart, so signing into one server in the window's session would sign
the window out of another.

## Window

```
┌────────────────────────────────────────────────────────────┐
│ ⌈γ⌉ Alpha · Rydberg lab ▾   Starting Beta…   ☁ Synced [↑ Restart to update] ⟳  – □ ✕ │  shell bar (38 px, is the title bar)
├────────────────────────────────────────────────────────────┤
│                                                            │
│   launcher (file://ui/launcher.html)                       │  content view
│   or the server's Gamma frontend (http://…)                │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

One `BaseWindow`, two `WebContentsView`s. The **shell bar** is the
frameless window's title bar (OS controls overlaid on Windows/Linux, traffic
lights inset on macOS). On Windows and Linux the bar keeps its right 146 px
free for those controls: the `titlebar-area` env() values reach only a
window's own page, never a view like the bar. The logo opens the launcher, which lists every
server (also `Ctrl/Cmd+Shift+L`). Then the switcher: click the name → dropdown of
the open server's Gamma workspaces (check on the current one, role or
*personal* per row, the clone chips), then every server (running /
reachable dot, check on the current one), then *All servers…* (the
launcher again). At the right: the sync button and its panel (above, "Kept
on this computer"), the update pill (only while an update is ready, see
below) and a reload button. One dropdown is open at a time. While one is,
the bar view is temporarily enlarged over the content (its page is
transparent outside the strip and the dropdown), which is how a 38 px view
can show a menu.

The **content view** shows the launcher or the server. The launcher lists
servers as cards (kind, running / reachable dot, size on disk, data dir /
URL, *last opened* badge) with open / rename / credentials / data folder /
server log / remove actions, then Settings: the *reopen last server at
launch* switch, the *Local server storage* row, the *Updates* row (status
line + check / download / restart button), and the dev-mode server
overrides.
When a server will not open, the launcher shows a **failure panel** instead
of a raw message: one sentence naming the cause, with the underlying line
(the server's log line, or Chromium's `ERR_…`) underneath. Below it sits the
button that would fix it: *Check for updates* when the data is newer than
the app, *Open data folder* when the files are the problem, *Try again*, and
*Show log file* for a local server. The run's output is folded away under
*Server output*. `lib/startup.js` does the matching: a sidecar that died
during startup from the lines it printed, a URL that would not load from
Chromium's error. Anything else falls back to the plain message. The storage
row shows the folder new local servers are created in. *Change…* opens a
folder picker and *Use default* appears once a custom folder is set. Either
one leads to a dialog with *Only new servers* and, when local servers exist
under the current root, *Move data*, which relocates them too.

**Theme.** The chrome paints in Gamma's own theme: the preload on server
pages mirrors the page's `data-theme` attribute (`dark`/`light`/`gamma-light`/
`gamma-dark`/`sepia`/`solarized`/`gray`) to the main process, which restyles the bar, the
launcher, the window background and the Windows title-bar overlay. The last
theme is persisted so the chrome is right before any page has loaded, and
with none of either — a first run, or the launcher, which reports no theme
— it follows the OS scheme (`main.js` `currentTheme`, repainting on
`nativeTheme`'s `updated`), since Gamma's own default theme is System: a
light machine never opens a dark window.

The tokens are Gamma's own. `ui/tokens.css` is a committed copy of
`frontend/src/shared/styles/tokens.css`, and `ui/fonts/` holds the Latin
subset of Inter. `npm run copy-tokens` in `frontend/` refreshes both, and
`frontend/tests/themes.test.mjs` fails while they differ. Gamma's tokens
derive a theme's colours from its scheme, so the shell pages set
`data-scheme` beside `data-theme`, from the same dark-theme list as Gamma's
`app/prefDefs.js`. The bar is Gamma's chrome colour (`--bg-page`, the same
as Gamma's topbar under it), and `main.js`'s title-bar palette repeats that
colour per theme. The same test checks both copies. The icons are the same
stroke glyphs as `frontend/src/shared/ui/Icons.jsx`.

### Background and tray

Closing the last window quits the app (macOS keeps the dock process, as
any Mac app) unless **Keep running in the background** is on. The setting
is `settings.background`, turned on by the launcher's switch, the *Server*
menu, the tray menu, or `offerBackground`, the offer made when a clone or a
folder on disk is first created. On, `window-all-closed` leaves the app
running with a tray icon (`ensureTray`: *Open Gamma*, the two switches,
*Quit Gamma*). At launch `startBackgroundHosts` then starts every local
server instead of only the clones' hosts, so clones and folders on disk
keep syncing with no window. The tray's click or *Open Gamma* brings the
window back where the user left off (`showWindow`). Turning the setting off
from the tray with no window quits.

**Start at login** (`settings.openAtLogin`) registers the OS login item
with `--hidden`, so a login start makes no window and goes straight to the
tray (`applyLoginItem`; macOS reports a hidden start itself through
`wasOpenedAsHidden`). On Windows and macOS that is `app.setLoginItemSettings`;
on Linux an autostart entry under `~/.config/autostart`. Packaged builds
only, and nothing under the test harness.

## In-app updates

`lib/updater.js` wraps `electron-updater`, VS Code style: a silent check
15 s after launch and every 4 h, download in the background, then one
attention-seeking control — the **Restart to update** pill in the shell bar
(and the same action in the launcher's Updates row). *Help → Check for
Updates…* is the only flow that answers with a dialog. The feed is the
GitHub Release (details and the signing caveat: [release.md](release.md)).

State machine (`update` in the shell state): `idle` → `checking` →
`up-to-date` | `downloading` (percent) → `downloaded` | `error`;
`unsupported` in dev builds, under the test harness and in a Microsoft
Store install (`process.windowsStore`; the Store delivers those updates,
see [release.md](release.md#microsoft-store)). On **Windows** the
update installs on restart (`quitAndInstall`, silent NSIS run; unsigned
builds are fine — electron-updater only verifies a publisher when one is
configured). On **Linux** (the `.deb`) the same flow runs through
electron-updater's `DebUpdater` (chosen from `resources/package-type`): the
new `.deb` downloads in the background and *Restart to update* installs it
with `dpkg -i` under `pkexec`, so the user gets a password prompt, then the
app relaunches. On **macOS** an unsigned app cannot self-update (Squirrel.Mac
requires a valid signature), so the shell reports `available` instead of
downloading and the pill / button opens the release page; `IN_APP_INSTALL`
in `lib/updater.js` is the switch to flip once the builds are signed.

## Shell state

Electron's userData dir (`%APPDATA%/gamma-desktop` /
`~/Library/Application Support/gamma-desktop` / `~/.config/gamma-desktop`;
the app shows the path at the
bottom of the launcher; a Microsoft Store install gets the MSIX-virtualized
copy under `%LOCALAPPDATA%\Packages\xwtim.GammaPDF_<hash>\LocalCache\Roaming`,
which the Store uninstall deletes — [release.md](release.md#microsoft-store)):

- `servers.json` — the registry: server list, `lastOpened`, `windowBounds`,
  and `settings` (`openLastOnLaunch`, `lastTheme`, `dataRoot`, `background`
  and `openAtLogin` (the tray, above), `folderHost` (the local server that
  keeps folders of remote servers), the dev-mode
  `pythonPath`/`backendDir`/`staticDir` overrides). Local admin credentials
  are stored in plaintext here — same trust level as the SQLite files next
  to it; acceptable for a per-OS-user desktop app. An older profile's
  `workspaces.json` (the file's previous name) is copied over on first start
  and left in place.
- `workspaces/<id>/` — local servers' data dirs (a standard `GAMMA_DATA_DIR`
  layout: `users.db`, `workspaces/<id>/{pages.db,data.db,uploads/}` — Gamma's
  own workspaces inside; the folder name predates the rename and stays so
  existing installs need no move). This is the default **storage root**.
  `settings.dataRoot` replaces it with any folder (a drive with room, a
  synced folder, outside an MSIX package's virtualized AppData), and new
  local servers are created under the current root as `<root>/<id>`.
- `logs/<id>.log` — captured stdout/stderr of each sidecar run.

Changing the root is `registry.setDataRoot(dir, { move })`. With `move` it
relocates every local server under the old root. The shell stops their
sidecars first (an open one returns to the launcher). The registry copies
each `<id>` dir with `fs.cpSync` (so it works across drives), and only after
every copy succeeded re-points the entries and deletes the originals. A
failure midway rolls back the copies and leaves the registry untouched. The
new folder may not be inside the current one or contain it. Removing a
server offers *keep files* / *delete everything*; deletion is guarded to
directories under the default or the configured root only.

`GAMMA_SHELL_USER_DATA=<dir>` relocates all of it (the tests use a temp
profile); `GAMMA_SHELL_DOWNLOAD_DIR=<dir>` saves downloads there without the
dialog and `GAMMA_SHELL_PICK_DIR=<dir>` answers the folder picker (tests
only); `GAMMA_SHELL_NO_UPDATE=1` disables the updater.

## File map

- `main.js` — window + views + layout, theme mirror, native menu
  (accelerators only; hidden behind Alt on Windows; *Help* holds *Check for
  Updates…*), IPC for the shell pages, auto-login script, navigation guard
  (only registered server origins may load in the content view; everything
  else — `target=_blank`, cross-origin redirects — opens in the system
  browser), the remote health probes, the `/api/session` read behind the
  workspace switcher, the clone and folder-on-this-computer flows over the
  server's API, the tray and background mode, `--smoke` self-test, sidecar
  cleanup on quit, the `GAMMA_SHELL_TEST` hook the e2e suite drives.
- `preload.js` — exposes the `gammaShell` IPC bridge **only on `file:`
  URLs**; on server pages it exposes nothing and only reports `data-theme`
  changes.
- `ui/tokens.css` — a copy of Gamma's design tokens (see Theme);
  `ui/fonts/` — Inter, Latin subset, with its licence.
- `ui/theme.css` — the Inter `@font-face`, Gamma's keyboard focus ring and
  its unified control classes with the app's recipes (`uiBtn` and its
  primary / ghost / danger / `sm` / `iconSq` variants, `ctlBtn`, `uiInput`,
  `switch`, the `dot` states) for the shell pages. The pages take every
  size, radius and shadow from the tokens (`--fs-*`, `--radius-*`,
  `--shadow-*`, `--font-mono`), icons at Gamma's 14 / 16 / 20 px with its
  1.6 px stroke, and section labels in Gamma's sentence case.
- `ui/bar.html` — the shell bar. `ui/launcher.html` — the server picker.
  Both plain HTML, no build step.
- `lib/registry.js` — `servers.json` load/save, add/rename/remove,
  last-opened, settings, window bounds, data-dir size.
- `lib/sidecar.js` — local server lifecycle: free port, spawn, health poll
  (`/api/health`, 60 s budget for frozen cold starts), log capture,
  tree-kill on Windows. Backend resolution order: explicit settings
  (pythonPath/backendDir) → bundled frozen server (packaged app) →
  repo auto-detect (`backend/venv` + `frontend/dist`, dev mode).
- `lib/startup.js` — why a sidecar would not start: the per-run tail of its
  log (the file is appended to across runs) and a rule table turning the
  server's last words into one sentence, the matched line, and the `action`
  the launcher offers as a button.
- `lib/updater.js` — the electron-updater wrapper described above.
- `lib/keeping.js` — what this computer keeps, read as items and one
  summary for the sync button ("Kept on this computer"); no Electron, unit
  tested by `test/keeping.test.js` (`npm test`).
- `electron-builder.cjs` — the packaging config (targets, extra resources,
  secret-gated signing, the update feed's `publish` block).
  `scripts/adhoc-sign.cjs` — its `afterPack` hook: ad-hoc signs a macOS
  build made without a certificate ([release.md](release.md)).
- `backend_entry.py` — entry for the frozen server (`--port`, `--data-dir`;
  sets env before importing gamma, serves the bundled `frontend_dist`).
- `build_backend.py` — PyInstaller onedir freeze into
  `dist-backend/gamma-server/` (collects uvicorn's string-resolved modules,
  pypdfium2's native lib, ziamath/ziafont fonts, and `frontend/dist`).
  Handles conda-based interpreters by adding `<base>/Library/bin` to the
  DLL search path.
- `assets/icon.png` — app icon (512 px, the favicon mark; electron-builder
  derives ico/icns and the Linux icon set). `assets/entitlements.mac.plist` —
  hardened-runtime entitlements for signed mac builds.
- `assets/appx/` and `assets/store/` — tracked Store package/listing images;
  `scripts/store-art.js` regenerates them. The packager's build-resources
  directory is `assets/`; `build/` holds generated PyInstaller intermediates.
- `test/e2e.js` — the Playwright-driven end-to-end suite (see
  [checklist.md](checklist.md)). `test/smoke.js` — runs the app's `--smoke`
  self-test (dev or `--packaged`).

## Invariants

- The shell must keep treating Gamma as a black box: talk to it only via the
  public HTTP API + env config (`GAMMA_DATA_DIR`, `GAMMA_STATIC_DIR`,
  `GAMMA_ADMIN_USER`, `GAMMA_ADMIN_PASSWORD`, `GAMMA_VERSION` = the shell's
  own version, so the server's admin dashboard names the app,
  `GAMMA_CLOUD_DEFAULT_ISSUER` = Gamma Cloud, the account server until the
  admin saves another, `GAMMA_FOLDER_LINKS` = this server keeps folders on
  disk, anywhere the user picks, since the sidecar runs as the user), `/api/health` and
  `/api/session` (+ the `?ws=` URL parameter). No imports from `backend/`,
  no frontend patches. The one thing it reads off the page is the
  `data-theme` attribute (read-only, via the preload).
- Server pages never get an IPC bridge; `gammaShell` exists only on the
  shell's own `file:` pages, and every handler re-checks the sender.
- Local sidecars bind `127.0.0.1` only (the LAN-exposed use case is the
  existing server/Docker deployment, not the desktop app).
- Navigation allowlist: only registered server origins render in the
  content view; foreign URLs (including `window.open` and cross-origin
  redirects) go to the system browser.
- The updater never installs without the user's click (the pill / button /
  dialog); automatic work is limited to checking and downloading.
