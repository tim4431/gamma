# Desktop app (`desktop/`)

The desktop app's developer docs live in the folder itself, separate from
Gamma's — the shell treats Gamma as a black box (HTTP API + env config), so
nothing in `backend/` or `frontend/` needs to know about it:

- [desktop/README.md](../../desktop/README.md) — overview + run / develop.
- [desktop/docs/architecture.md](../../desktop/docs/architecture.md) —
  servers (local sidecar servers + remote URLs) and Gamma's workspaces in
  the switcher, the window (shell bar + content view), remote reachability
  probes, files and folders opened from the file manager, in-app updates,
  shell state, file map, invariants.
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
focus), a work folder made with `choose` (paused, choosing, no round) and
the `?ws=<id>&folder=<id>&track=<link id>` URL the shell then loads, on
which the web app's tracking dialog chooses its files through the server
([work_folders.md](work_folders.md)); for a file or a folder opened with
the app, `POST /api/folder-links/where` on each local server (what the path
is to Gamma, for the account the shell asks as: a vault's directory or
file, around vaults, a vault of one file, or nobody's with the place's
refusal, [synced_vaults.md](synced_vaults.md#which-path-is-what)), `GET
/api/folder-links/<id>` (whether a work folder the app made still
chooses), the `?ws=<id>&folder=<id>` URL of a directory's folder and
`&tick=<path>` beside `track` (a file to tick in the tracking dialog, its
path relative to the work folder), `POST /api/folder-links/open` on a
local server (a file of any format read where it is, under *Opened
files*: `{workspace, page, created, title, format}`), or for a PDF with the
`openAddsToLibrary` entry of the account's profile on, and in the Mac App
Store build, the upload routes and `POST /api/blocks/by-doc/<hash>`; `POST
/api/import/pdf-annotations`, the `embAnnots` entry of the account's
profile and the `?ws=<id>&page=<id>` URL ([architecture.md](../../desktop/docs/architecture.md)
"Opening files and folders"); a page's document, `properties.doc` of `GET
/api/blocks/<id>` (its `format`, `hash`, `ext` and `copy`: an opened PDF's
hash for its annotations, a carried page's own PDF and its stored name in
the export), read of the app's own local server only, which is always the
shell's release; the change feed's `format` (`GET
/api/sync/changes?limit=1`) of both servers before a carry, which refuses
a target of an older one; and the `data-theme` attribute on `<html>` (the shell
mirrors it into its own chrome).

The shell sets no proxy. Its own traffic (the windows, the remote
servers' health probes, the updater) is Chromium's and follows the OS
proxy settings, PAC included. A local server's outbound connections follow
that server's own Network proxy setting ([network_proxy.md](network_proxy.md));
it starts with the shell's whole environment, so its "Use system
settings" reads the `*_proxy` variables, else the Windows or macOS proxy
settings, and never PAC. The pane says so on the desktop.

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
