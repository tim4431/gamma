# Desktop app (`desktop/`)

The desktop app's developer docs live in the folder itself, separate from
Gamma's — the shell treats Gamma as a black box (HTTP API + env config), so
nothing in `backend/` or `frontend/` needs to know about it:

- [desktop/README.md](../../desktop/README.md) — overview + run / develop.
- [desktop/docs/architecture.md](../../desktop/docs/architecture.md) —
  servers (local sidecar servers + remote URLs) and Gamma's workspaces in
  the switcher, the window (shell bar + content view), remote reachability
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
server's build), `GAMMA_UPDATES` (`desktop`, in a Microsoft Store
install `store`, in the Mac App Store build `mas`, which also sets
`GAMMA_UPDATE_CHECK=off`: who installs a newer release, for the Server dashboard,
[user_db.md](user_db.md) "Server dashboard and log"), `GAMMA_CLOUD_DEFAULT_ISSUER` (Gamma Cloud, so sign-in with
it is on until the admin saves another account server or none,
[cloud_accounts.md](cloud_accounts.md)), `GAMMA_FOLDER_LINKS` (this server keeps
synced vaults and reads PDFs where they are, anywhere the user picks,
[synced_vaults.md](synced_vaults.md#the-desktop-app)), `GAMMA_SHELL_SECRET`
(made anew at each start of the app: the routes that take a path on this
computer answer only a request that carries it as `X-Gamma-Shell`, so a page
of Gamma never names a path, [api.md](api.md) "Synced vaults and PDFs read
where they are"); `/api/health` (a 200, or the 503 with
`error: data_directory_not_upgradable` a server answers when it cannot
upgrade its data directory — up either way, and the window then shows that
server's guidance page, [migrations.md](migrations.md)); `/api/session` +
`/api/login` for the silent first login; the `/api/folder-links` routes
(the sync panel, the vault dialog, `POST /hint` when a window gets the
focus); for a PDF opened with the app, `POST /api/folder-links/open` on a
local server (read where it is, under *Opened files*), or with the
`openAddsToLibrary` entry of the account's profile on, and in the Mac App
Store build, the upload routes and `POST /api/blocks/by-doc/<hash>`; `POST
/api/import/pdf-annotations`, the `embAnnots` entry of the account's
profile and the `?ws=<id>&page=<id>` URL ([architecture.md](../../desktop/docs/architecture.md)
"Opening PDF files"); and the `data-theme` attribute on `<html>` (the shell
mirrors it into its own chrome).

The iPad and Android apps run the same sidecar model: the same backend
inside the app, started with these variables except `GAMMA_FOLDER_LINKS`
and `GAMMA_SHELL_SECRET` ([ipad.md](ipad.md#the-local-server), [android.md](android.md)). They
also wear the shell's chrome: `desktop/ui/bar.html` and `launcher.html`
are copied into each app and answered by a `gammaShell` of its own, in
Swift and in Kotlin ([ipad.md](ipad.md#the-chrome)), so a change to
either page is a change to all three shells. The apps hide what only the
desktop does by their state (no `keeping`, `update` or `settings`) and by
one rule in `ui/theme.css` (`body[data-touch] .desktopOnly`; each page's
`SHELLS` table sets `data-touch` for the `ipad` and `android` platforms).
The bar's hide chevron is the apps' alone, shown by a key only their
state has (`barHideable`). The bar closes its menu on a press outside it
in every shell, since iOS sends no click for a tap on its see-through page
([architecture.md "Window"](../../desktop/docs/architecture.md#window)).
