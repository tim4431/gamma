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
a target of an older one; the routes of `/api/remote-servers` on its own
local servers ([https.md](https.md)): `POST /check` before each token it
mints for another server, `POST /move` (with `dry_run` first) when a
server's address changes, `PUT /pins` with every certificate the person
trusted by its fingerprint; the status fields of the folder-links,
computer-links and mirrors items (`error_kind`, `move_to`, `upgrade`,
`cert`, `blocked`) and the folder-links and computer-links items' `plain`;
and the `data-theme` attribute on `<html>` (the shell mirrors it into its
own chrome).

## Addresses and certificates

The shell's half of [https.md](https.md), in full in
[architecture.md "A remote's address and certificate"](../../desktop/docs/architecture.md#a-remotes-address-and-certificate):

- **No redirects.** `serverApi` passes `redirect: 'error'` for every call
  it makes (mints, revokes, `move`, `pins`, `check`).
- **Exact identity.** A view is a given server only when its origin equals
  the link's stored origin; every mint site and every `remote_url` it sends
  takes the origin from `remoteOf`, which refuses an address the links moved
  away from, and a load of such an address is rerouted to the current one
  (`registry.currentOrigin`). **The rule for step 5b:** a page-originated ask
  (a terminal, Show in folder, Ask on this computer) is honoured only from a
  view whose origin equals the link's origin exactly and whose load was
  https that verified or is pinned, or loopback.
- **Moves.** `moveServer`: the host's dry run; for an `https://` address
  its certificate as the window sees it (`trustMoveTarget`: a pin of the
  same host name carried, one a pin may answer asked with its fingerprint
  and pinned for the new origin, dropped again should nothing move); the
  question naming the tokens (and the unencrypted line for `http://`,
  `plain_ok`), the move, the replaced tokens revoked at the new address,
  then the registry (`moved`; the old address's pin carried on the same
  host name, dropped for another). A redirect to the same host name over
  https moves by itself once a session (its links alone when that address
  is listed already); a failed one offers *Use <https address>*. An http
  address that sends the window to https on its host (HSTS, a redirect) is
  followed nowhere, and its launcher card offers *Use <that address>*. Only the
  host's links move: an offline copy of the old address that a local
  server other than the host keeps stays there (no form changes a copy's
  address; [https.md](https.md) "Moving a server's address").
- **Plain http.** `beforeMint` asks the host's `check`: refused for a
  public host on a new link, asked each time for an existing one, asked
  once per origin of this network (`settings.plainAccepted`).
- **Pins.** `setCertificateVerifyProc` answers with `certs.decide` (only
  `ERR_CERT_AUTHORITY_INVALID`, `_COMMON_NAME_INVALID`, `_DATE_INVALID`,
  never under the Gamma Cloud's domain, the leaf's SHA-256 pinned for any
  port of the host; the network service keeps its answers for the session,
  so `certificate-error` accepts a refusal from before the pin by the same
  rule, and a forgotten pin's certificate stays accepted until the next
  start); a `certificate-error` record with a nonce backs the
  launcher's card, whose button pins only that record; `PUT
  /api/remote-servers/pins` hands the list to each running local server and
  each one that starts, and again when a pin is forgotten or its server
  removed; *Trust it for syncing too* pins what the window
  already accepted. The registry keeps `pins`, `moved`, a remote's
  `httpsSeen` and `settings.plainAccepted`; `withScheme` follows the shared
  host table (`lib/hostScope.js`).
- **Text only.** What a server or a certificate says of itself (subject,
  issuer, fingerprint, `last_error`, `move_to`) is set with `textContent`
  or `title` in `launcher.html` and `bar.html`, capped where it is read.

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
