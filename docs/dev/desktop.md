# Desktop app (`desktop/`)

The desktop app's developer docs live in the folder itself, separate from
Gamma's — the shell treats Gamma as a black box (HTTP API + env config), so
nothing in `backend/` or `frontend/` needs to know about it:

- [desktop/README.md](../../desktop/README.md) — overview + run / develop.
- [desktop/docs/architecture.md](../../desktop/docs/architecture.md) — the
  workspace model (local sidecar servers + remote URLs), the window (shell
  bar with the workspace switcher + content view), remote reachability
  probes, in-app updates, shell state, file map, invariants.
- [desktop/docs/release.md](../../desktop/docs/release.md) — package, the
  `desktop` workflow, secret-gated code signing, the auto-update feed,
  distribution alternatives. All workflows side by side:
  [github_actions.md](github_actions.md).
- [desktop/docs/checklist.md](../../desktop/docs/checklist.md) — the
  pre-release QA checklist and what `npm run e2e` / `npm run e2e:packaged`
  cover.

The only contract Gamma keeps for the shell: the env variables
`GAMMA_DATA_DIR`, `GAMMA_STATIC_DIR`, `GAMMA_ADMIN_USER`,
`GAMMA_ADMIN_PASSWORD`, `GAMMA_VERSION` (the shell's version, shown as the
server's build), `GAMMA_UPDATES` (`desktop` or, in a Microsoft Store
install, `store`: who installs a newer release, for the Server dashboard,
[user_db.md](user_db.md) "Server dashboard and log"), `GAMMA_CLOUD_DEFAULT_ISSUER` (Gamma Cloud, so sign-in with
it is on until the admin saves another account server or none,
[cloud_accounts.md](cloud_accounts.md)), `GAMMA_FOLDER_LINKS` (this server keeps
folders on disk, anywhere the user picks,
[folder_sync.md](folder_sync.md) "Folders kept by the desktop app"); `/api/health` (a 200, or the 503 with
`error: data_directory_not_upgradable` a server answers when it cannot
upgrade its data directory — up either way, and the window then shows that
server's guidance page, [migrations.md](migrations.md)); `/api/session` +
`/api/login` for the silent first login; for a PDF opened with the app,
the upload routes, `POST /api/blocks/by-doc/<hash>`, `POST
/api/import/pdf-annotations`, the `embAnnots` entry of the account's
profile and the `?ws=<id>&page=<id>` URL ([architecture.md](../../desktop/docs/architecture.md)
"Opening PDF files"); and the `data-theme` attribute on `<html>` (the shell
mirrors it into its own chrome).

The iPad app runs the same sidecar model: the same backend inside the app,
started with these variables except `GAMMA_FOLDER_LINKS`
([ipad.md](ipad.md#the-local-server)). It also wears the shell's chrome:
`desktop/ui/bar.html` and `launcher.html` are copied into its bundle and
answered by a `gammaShell` in Swift ([ipad.md](ipad.md#the-chrome)), so a
change to either page is a change to both shells. The iPad hides what
only the desktop does by its state (no `keeping`, `update` or `settings`)
and by one rule in `ui/theme.css` (`body[data-platform="ipad"]
.desktopOnly`), and both shells close the bar's menu on a press outside
it, since iOS sends no click for a tap on its see-through page
([architecture.md "Window"](../../desktop/docs/architecture.md#window)).
