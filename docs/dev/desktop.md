# Desktop app (`desktop/`)

The Windows/macOS desktop app. It is deliberately a **thin shell**: Gamma's
backend and frontend are completely untouched — the shell is an Electron
window plus a process manager, and the app the window shows always loads from
the Gamma server it is connected to (exactly like a browser tab). There is no
API-base plumbing, no CORS, and no version skew: each workspace serves its own
matching frontend.

## Workspaces

A **workspace is a Gamma server**. Two kinds:

- **Local** — a data directory under the shell's userData dir, served by a
  Gamma backend the shell spawns on `127.0.0.1:<free port>`
  (`GAMMA_DATA_DIR` pointed at the workspace). Fully self-initializing: the
  server seeds schema + first admin on an empty data dir. The shell passes
  `GAMMA_ADMIN_USER`/`GAMMA_ADMIN_PASSWORD` on first spawn (the server's
  one-time password print would be lost in the hidden console), remembers the
  credentials in its registry, and auto-logs-in after navigation by POSTing
  `/api/login` from the page. Several local workspaces = several data dirs,
  one server process each.
- **Remote** — just a URL (e.g. the NAS Docker deployment). Normal login;
  the session cookie persists in the Electron profile per origin.

Workspaces are fully independent servers; there is **no synchronization**.

Shell state lives in Electron's userData dir (`%APPDATA%/gamma-desktop` /
`~/Library/Application Support/gamma-desktop`):

- `workspaces.json` — the registry (workspace list + local-server settings).
  Local admin credentials are stored in plaintext here — same trust level as
  the SQLite files next to it; acceptable for a per-OS-user desktop app.
- `workspaces/<id>/` — local workspace data dirs (a standard `GAMMA_DATA_DIR`
  layout: `users.db`, `users/<name>/…`).
- `logs/<id>.log` — captured stdout/stderr of each sidecar run.

## File map

- `main.js` — window + native menu (Workspace switcher, `Ctrl/Cmd+Shift+L`
  back to launcher), IPC for the launcher, auto-login script, navigation
  guard (only workspace origins may load in-window; everything else opens in
  the system browser), `--smoke` self-test, sidecar cleanup on quit.
- `lib/registry.js` — `workspaces.json` load/save, add/remove, settings.
- `lib/sidecar.js` — local server lifecycle: free port, spawn, health poll
  (`/api/health`, 60 s budget for frozen cold starts), log capture,
  tree-kill on Windows. Backend resolution order: explicit settings
  (pythonPath/backendDir) → bundled frozen server (packaged app) →
  repo auto-detect (`backend/venv` + `frontend/dist`, dev mode).
- `preload.js` — exposes the `gammaShell` IPC bridge **only on `file:` URLs**;
  workspace servers never see it (the main process re-checks the sender too).
- `launcher/index.html` — the self-contained workspace picker (no build
  step): cards with running-state, create local / add remote dialogs,
  credentials + data-folder reveal, advanced local-server settings.
- `backend_entry.py` — entry for the frozen server (`--port`, `--data-dir`;
  sets env before importing gamma, serves the bundled `frontend_dist`).
- `build_backend.py` — PyInstaller onedir freeze into
  `dist-backend/gamma-server/` (collects uvicorn's string-resolved modules,
  pypdfium2's native lib, ziamath/ziafont fonts, and `frontend/dist`).
  Handles conda-based interpreters by adding `<base>/Library/bin` to the DLL
  search path.
- `build/icon.png` — app icon (512px; electron-builder derives ico/icns).

## Run / develop

```bash
cd desktop && npm install       # once (needs the fnm node on PATH)
npm start                       # dev mode: sidecars run from backend/venv,
                                # frontend served from frontend/dist (build it first)
npm run smoke                   # headless e2e: temp workspace → health → auto-login
```

On Windows, `desktop\start.cmd` launches without any PATH setup — it finds
the fnm-managed node itself and clears `ELECTRON_RUN_AS_NODE` (pass `--smoke`
to run the self-test instead).

Dev-mode gotchas: unset `ELECTRON_RUN_AS_NODE` if launching from inside an
Electron-based tool (VSCode terminals set it and Electron then runs as plain
Node). The smoke test prints one `SMOKE {...}` JSON line and exits 0/1.

## Package / release

```bash
python desktop/build_backend.py   # freeze backend (venv python; ~50 MB onedir)
cd desktop && npm run pack        # unpacked app in dist/win-unpacked (fast test)
npm run dist                      # real installer (NSIS .exe / .dmg)
```

`electron-builder` copies `dist-backend/gamma-server` into
`resources/gamma-server`; a packaged app is fully self-contained (no Python,
no Node on the user's machine). The packaged binary also honors `--smoke`.

CI: `.github/workflows/desktop-release.yml` — tag `desktop-v*` builds
Windows + macOS installers (frontend build → backend freeze → frozen-server
health check → electron-builder) and attaches them to a GitHub Release;
`workflow_dispatch` produces artifacts only. macOS builds are unsigned:
first launch is right-click → Open (or
`xattr -dr com.apple.quarantine /Applications/Gamma.app`).

## Invariants

- The shell must keep treating Gamma as a black box: talk to it only via the
  public HTTP API + env config (`GAMMA_DATA_DIR`, `GAMMA_STATIC_DIR`,
  `GAMMA_ADMIN_USER`, `GAMMA_ADMIN_PASSWORD`). No imports from `backend/`,
  no frontend patches.
- Local sidecars bind `127.0.0.1` only (the LAN-exposed use case is the
  existing server/Docker deployment, not the desktop app).
- `will-navigate` allowlist: only registered workspace origins render in the
  window; foreign URLs go to the system browser.
