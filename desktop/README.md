# Gamma desktop (`desktop/`)

The Windows/macOS app. It is deliberately a **thin shell**: Gamma's backend
and frontend are untouched — the shell is an Electron window plus a process
manager, and the app the window shows always loads from the Gamma server it
is connected to (exactly like a browser tab). No API-base plumbing, no CORS,
no version skew: each workspace serves its own matching frontend.

This folder is self-contained: its dev docs are this file and
[CHECKLIST.md](CHECKLIST.md) (what to verify before a release, and the
`npm run e2e` suite that does most of it). The rest of the repo only knows
the shell through the black-box contract in *Invariants* below.

## Workspaces

A **workspace is a Gamma server**. Two kinds:

- **Local** — a data directory under the shell's userData dir, served by a
  Gamma backend the shell spawns on `127.0.0.1:<free port>` with
  `GAMMA_DATA_DIR` pointed at it. Fully self-initializing: the server seeds
  schema + first admin on an empty data dir. The shell passes
  `GAMMA_ADMIN_USER`/`GAMMA_ADMIN_PASSWORD` on the first spawn (the server's
  one-time password print would be lost in the hidden console), remembers
  the credentials in its registry, and auto-logs-in after navigation by
  POSTing `/api/login` from the page. Several local workspaces = several
  data dirs, one server process each; a sidecar keeps running until the app
  quits, so switching back is instant.
- **Remote** — just a URL (e.g. the NAS Docker deployment). Normal login;
  the session cookie persists in the Electron profile per origin.

Workspaces are fully independent servers; there is **no synchronization**
(move data between them with Gamma's own *Export my data* / *Import data*).

## Window

```
┌────────────────────────────────────────────────────────────┐
│ ⌈γ⌉ Alpha ▾   Starting Beta…                     ⟳  – □ ✕ │  shell bar (38 px, is the title bar)
├────────────────────────────────────────────────────────────┤
│                                                            │
│   launcher (file://ui/launcher.html)                       │  content view
│   or the workspace's Gamma frontend (http://…)             │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

One `BaseWindow`, two `WebContentsView`s. The **shell bar** is the
frameless window's title bar (OS controls overlaid on Windows/Linux, traffic
lights inset on macOS) and holds the workspace switcher: click the name →
dropdown of every workspace (running dot for local ones, check on the current
one) plus *All workspaces…* (the launcher, also `Ctrl/Cmd+Shift+L`). While
the dropdown is open the bar view is temporarily enlarged over the content
(its page is transparent outside the strip and the menu), which is how a
38 px view can show a menu. A reload button sits at the right.

The **content view** shows the launcher or the workspace. The launcher lists
workspaces as cards (kind, running state, size on disk, data dir / URL,
*last opened* badge) with open / rename / credentials / data folder / server
log / remove actions, plus the *reopen last workspace at launch* switch and
the dev-mode server overrides.

**Theme.** The chrome paints in Gamma's own theme: the preload on workspace
pages mirrors the page's `data-theme` attribute (`dark`/`light`/`sepia`/
`gray`; none = dark) to the main process, which restyles the bar, the
launcher, the window background and the Windows title-bar overlay. The last
theme is persisted so the chrome is right before any page has loaded. The
tokens in `ui/theme.css` are copies of `frontend/src/app.css`'s, and the
icons are the same stroke glyphs as `frontend/src/icons.jsx`.

## Shell state

Electron's userData dir (`%APPDATA%/gamma-desktop` /
`~/Library/Application Support/gamma-desktop`; the app shows the path at the
bottom of the launcher):

- `workspaces.json` — the registry: workspace list, `lastOpened`,
  `windowBounds`, and `settings` (`openLastOnLaunch`, `lastTheme`, the
  dev-mode `pythonPath`/`backendDir`/`staticDir` overrides). Local admin
  credentials are stored in plaintext here — same trust level as the SQLite
  files next to it; acceptable for a per-OS-user desktop app.
- `workspaces/<id>/` — local workspace data dirs (a standard `GAMMA_DATA_DIR`
  layout: `users.db`, `users/<name>/{pages.db,data.db,uploads/}`). Removing a
  workspace offers *keep files* / *delete everything*; deletion is guarded
  to directories under this folder only.
- `logs/<id>.log` — captured stdout/stderr of each sidecar run.

`GAMMA_SHELL_USER_DATA=<dir>` relocates all of it (the tests use a temp
profile); `GAMMA_SHELL_DOWNLOAD_DIR=<dir>` saves downloads there without the
dialog (tests only).

## File map

- `main.js` — window + views + layout, theme mirror, native menu
  (accelerators only; hidden behind Alt on Windows), IPC for the shell pages,
  auto-login script, navigation guard (only workspace origins may load in
  the content view; everything else — `target=_blank`, cross-origin
  redirects — opens in the system browser), `--smoke` self-test, sidecar
  cleanup on quit, the `GAMMA_SHELL_TEST` hook the e2e suite drives.
- `preload.js` — exposes the `gammaShell` IPC bridge **only on `file:`
  URLs**; on workspace pages it exposes nothing and only reports
  `data-theme` changes.
- `ui/theme.css` — Gamma's tokens + the unified control classes (`uiBtn`,
  `ctlBtn`, `uiInput`) for the shell pages.
- `ui/bar.html` — the shell bar. `ui/launcher.html` — the workspace picker.
  Both plain HTML, no build step.
- `lib/registry.js` — `workspaces.json` load/save, add/rename/remove,
  last-opened, settings, window bounds, data-dir size.
- `lib/sidecar.js` — local server lifecycle: free port, spawn, health poll
  (`/api/health`, 60 s budget for frozen cold starts), log capture,
  tree-kill on Windows. Backend resolution order: explicit settings
  (pythonPath/backendDir) → bundled frozen server (packaged app) →
  repo auto-detect (`backend/venv` + `frontend/dist`, dev mode).
- `backend_entry.py` — entry for the frozen server (`--port`, `--data-dir`;
  sets env before importing gamma, serves the bundled `frontend_dist`).
- `build_backend.py` — PyInstaller onedir freeze into
  `dist-backend/gamma-server/` (collects uvicorn's string-resolved modules,
  pypdfium2's native lib, ziamath/ziafont fonts, and `frontend/dist`).
  Handles conda-based interpreters by adding `<base>/Library/bin` to the
  DLL search path.
- `build/icon.png` — app icon (512 px, the favicon mark; electron-builder
  derives ico/icns).
- `test/e2e.js` — the Playwright-driven end-to-end suite (see CHECKLIST).
  `test/smoke.js` — runs the app's `--smoke` self-test (dev or `--packaged`).

## Run / develop

```bash
cd desktop && npm install       # once (needs the fnm node on PATH)
npm start                       # dev mode: sidecars run from backend/venv,
                                # frontend served from frontend/dist (build it first)
npm run smoke                   # quick self-test: temp workspace → health → auto-login
npm run e2e                     # full suite (CHECKLIST.md), ~3 min
```

On Windows, `desktop\start.cmd` launches without any PATH setup — it finds
the fnm-managed node itself and clears `ELECTRON_RUN_AS_NODE` (pass
`--smoke` to run the self-test instead).

Dev-mode gotchas: unset `ELECTRON_RUN_AS_NODE` if launching from inside an
Electron-based tool (VSCode terminals set it and Electron then runs as plain
Node). The smoke test prints one `SMOKE {...}` JSON line and exits 0/1.

## Package / release

```bash
python desktop/build_backend.py   # freeze backend (venv python; ~50 MB onedir)
cd desktop && npm run pack        # unpacked app in dist/win-unpacked (fast test)
npm run e2e:packaged              # the suite against the frozen bundle
npm run dist                      # real installer (NSIS .exe / .dmg)
```

`electron-builder` copies `dist-backend/gamma-server` into
`resources/gamma-server`; a packaged app is fully self-contained (no Python,
no Node on the user's machine). Installers are named
`Gamma-<version>-<os>-<arch>.<ext>`.

**Releasing** is one workflow, `.github/workflows/release.yml`, run by hand
from the Actions tab (or `gh workflow run release.yml --ref main`; the
`release` skill wraps it). It builds Windows + macOS installers (frontend
build → backend freeze → frozen-server health check → electron-builder →
packaged `--smoke`), zips the browser extension, and publishes everything as
ONE GitHub Release `Gamma <version>` — creating the `v<version>` tag itself,
so no tags are pushed by hand. The version is `desktop/package.json`'s
(bump it before releasing; a version that already has a tag is refused);
the extension zip carries `extension/manifest.json`'s own version. Inputs:
`version` override, `prerelease`, and `publish=false` for artifacts only.
The Docker image is a separate workflow (`docker.yml`, every push to
`main`). macOS builds are unsigned: first launch is right-click → Open (or
`xattr -dr com.apple.quarantine /Applications/Gamma.app`); Windows shows
SmartScreen's *More info → Run anyway*.

## Invariants

- The shell must keep treating Gamma as a black box: talk to it only via the
  public HTTP API + env config (`GAMMA_DATA_DIR`, `GAMMA_STATIC_DIR`,
  `GAMMA_ADMIN_USER`, `GAMMA_ADMIN_PASSWORD`). No imports from `backend/`,
  no frontend patches. The one thing it reads off the page is the
  `data-theme` attribute (read-only, via the preload).
- Workspace pages never get an IPC bridge; `gammaShell` exists only on the
  shell's own `file:` pages, and every handler re-checks the sender.
- Local sidecars bind `127.0.0.1` only (the LAN-exposed use case is the
  existing server/Docker deployment, not the desktop app).
- Navigation allowlist: only registered workspace origins render in the
  content view; foreign URLs (including `window.open` and cross-origin
  redirects) go to the system browser.
