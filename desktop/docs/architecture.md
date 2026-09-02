# Desktop shell architecture

The shell is an Electron window plus a process manager. Gamma stays a black
box: the window shows whatever Gamma server a workspace points at, the way a
browser tab would.

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

### Remote reachability

Local workspaces show a running dot (is the sidecar up). Remote ones show
the same dot from a **health probe**: the main process fetches
`<url>/api/health` (public, no session needed; 5 s timeout) per remote
workspace, caches the answer for 20 s, and exposes it as `reachable`
(`true` / `false` / `null` while unknown) in the shell state. Probes run on
demand — every launcher refresh and every bar-menu open ask for the list,
which triggers stale probes — and their results arrive asynchronously
through `pushState`, so the dot fills in a moment after the page renders.
Opening a remote workspace also records the outcome (success → reachable,
failure → unreachable). Green = reachable, red = unreachable, dim = not
probed yet.

## Window

```
┌────────────────────────────────────────────────────────────┐
│ ⌈γ⌉ Alpha ▾   Starting Beta…        [↑ Restart to update] ⟳  – □ ✕ │  shell bar (38 px, is the title bar)
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
dropdown of every workspace (running / reachable dot, check on the current
one) plus *All workspaces…* (the launcher, also `Ctrl/Cmd+Shift+L`). While
the dropdown is open the bar view is temporarily enlarged over the content
(its page is transparent outside the strip and the menu), which is how a
38 px view can show a menu. At the right: the update pill (only while an
update is ready, see below) and a reload button.

The **content view** shows the launcher or the workspace. The launcher lists
workspaces as cards (kind, running / reachable dot, size on disk, data dir /
URL, *last opened* badge) with open / rename / credentials / data folder /
server log / remove actions, then Settings: the *reopen last workspace at
launch* switch, the *Updates* row (status line + check / download / restart
button), and the dev-mode server overrides.

**Theme.** The chrome paints in Gamma's own theme: the preload on workspace
pages mirrors the page's `data-theme` attribute (`dark`/`light`/`sepia`/
`gray`; none = dark) to the main process, which restyles the bar, the
launcher, the window background and the Windows title-bar overlay. The last
theme is persisted so the chrome is right before any page has loaded. The
tokens in `ui/theme.css` are copies of `frontend/src/app.css`'s, and the
icons are the same stroke glyphs as `frontend/src/icons.jsx`.

## In-app updates

`lib/updater.js` wraps `electron-updater`, VS Code style: a silent check
15 s after launch and every 4 h, download in the background, then one
attention-seeking control — the **Restart to update** pill in the shell bar
(and the same action in the launcher's Updates row). *Help → Check for
Updates…* is the only flow that answers with a dialog. The feed is the
GitHub Release (details and the signing caveat: [release.md](release.md)).

State machine (`update` in the shell state): `idle` → `checking` →
`up-to-date` | `downloading` (percent) → `downloaded` | `error`;
`unsupported` in dev builds and under the test harness. On **Windows** the
update installs on restart (`quitAndInstall`, silent NSIS run; unsigned
builds are fine — electron-updater only verifies a publisher when one is
configured). On **macOS** an unsigned app cannot self-update (Squirrel.Mac
requires a valid signature), so the shell reports `available` instead of
downloading and the pill / button opens the release page; `IN_APP_INSTALL`
in `lib/updater.js` is the switch to flip once the builds are signed.

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
dialog (tests only); `GAMMA_SHELL_NO_UPDATE=1` disables the updater.

## File map

- `main.js` — window + views + layout, theme mirror, native menu
  (accelerators only; hidden behind Alt on Windows; *Help* holds *Check for
  Updates…*), IPC for the shell pages, auto-login script, navigation guard
  (only workspace origins may load in the content view; everything else —
  `target=_blank`, cross-origin redirects — opens in the system browser),
  the remote health probes, `--smoke` self-test, sidecar cleanup on quit,
  the `GAMMA_SHELL_TEST` hook the e2e suite drives.
- `preload.js` — exposes the `gammaShell` IPC bridge **only on `file:`
  URLs**; on workspace pages it exposes nothing and only reports
  `data-theme` changes.
- `ui/theme.css` — Gamma's tokens + the unified control classes (`uiBtn`,
  `ctlBtn`, `uiInput`, the `dot` states) for the shell pages.
- `ui/bar.html` — the shell bar. `ui/launcher.html` — the workspace picker.
  Both plain HTML, no build step.
- `lib/registry.js` — `workspaces.json` load/save, add/rename/remove,
  last-opened, settings, window bounds, data-dir size.
- `lib/sidecar.js` — local server lifecycle: free port, spawn, health poll
  (`/api/health`, 60 s budget for frozen cold starts), log capture,
  tree-kill on Windows. Backend resolution order: explicit settings
  (pythonPath/backendDir) → bundled frozen server (packaged app) →
  repo auto-detect (`backend/venv` + `frontend/dist`, dev mode).
- `lib/updater.js` — the electron-updater wrapper described above.
- `electron-builder.js` — the packaging config (targets, extra resources,
  secret-gated signing, the update feed's `publish` block).
- `backend_entry.py` — entry for the frozen server (`--port`, `--data-dir`;
  sets env before importing gamma, serves the bundled `frontend_dist`).
- `build_backend.py` — PyInstaller onedir freeze into
  `dist-backend/gamma-server/` (collects uvicorn's string-resolved modules,
  pypdfium2's native lib, ziamath/ziafont fonts, and `frontend/dist`).
  Handles conda-based interpreters by adding `<base>/Library/bin` to the
  DLL search path.
- `build/icon.png` — app icon (512 px, the favicon mark; electron-builder
  derives ico/icns). `build/entitlements.mac.plist` — hardened-runtime
  entitlements for signed mac builds.
- `test/e2e.js` — the Playwright-driven end-to-end suite (see
  [checklist.md](checklist.md)). `test/smoke.js` — runs the app's `--smoke`
  self-test (dev or `--packaged`).

## Invariants

- The shell must keep treating Gamma as a black box: talk to it only via the
  public HTTP API + env config (`GAMMA_DATA_DIR`, `GAMMA_STATIC_DIR`,
  `GAMMA_ADMIN_USER`, `GAMMA_ADMIN_PASSWORD`) and `/api/health`. No imports
  from `backend/`, no frontend patches. The one thing it reads off the page
  is the `data-theme` attribute (read-only, via the preload).
- Workspace pages never get an IPC bridge; `gammaShell` exists only on the
  shell's own `file:` pages, and every handler re-checks the sender.
- Local sidecars bind `127.0.0.1` only (the LAN-exposed use case is the
  existing server/Docker deployment, not the desktop app).
- Navigation allowlist: only registered workspace origins render in the
  content view; foreign URLs (including `window.open` and cross-origin
  redirects) go to the system browser.
- The updater never installs without the user's click (the pill / button /
  dialog); automatic work is limited to checking and downloading.
