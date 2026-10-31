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
  Gamma backend the shell spawns on `127.0.0.1:<port>` with
  `GAMMA_DATA_DIR` pointed at it (the port below, "A local server's
  address"). Fully self-initializing: the server seeds
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

### A local server's address

A local server keeps its port. `startLocal` in `main.js` starts every
sidecar. It asks for the port the server had last time (`port` on its
registry entry) and saves the one it got. `lib/sidecar.js` `pickPort` uses
that port while it is free. Otherwise it walks the IANA dynamic range
(49152–65535) from a random point to the first free port no other local
server has saved. The OS's own choice is not used, because Windows may hand
out any port above 1024: a port kept for good should not be 9001 or 5173.
So the address stays the same across restarts and relaunches. That is the
address a browser's Gamma Connector, a bookmark or a script was given
([docs/dev/extension.md](../../docs/dev/extension.md) "Auth and
permissions"). A port another program took by then is given up for a new
one, and the new address shows in the places below.

The address of a running local server shows beside its name in the bar
menu (`127.0.0.1:<port>`) and on its launcher card. The card has a *Copy
address* button (`shell:copy-address`; under the test harness the copy is
only recorded) and the credentials dialog names it too. In the browser the
Connector signs in with those credentials, since the window's session lives
in Electron's cookie jar, not the browser's.

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

### Folders and files on this computer

A folder of any server's workspace can be kept as a directory anywhere on
this computer: each paper's PDF beside a Markdown note of its highlights
and notes, kept up to date by a local server, and when asked, the PDFs
changed there sent back. A page can also follow one PDF file on this
computer, a manuscript's build output say: each new version of the file
becomes the page's PDF. Only the app's own local servers keep folders and
files on disk ([docs/dev/folder_sync.md](../../docs/dev/folder_sync.md)
"Folders kept by the desktop app"); a NAS only answers the reads, the
uploads and the replacement. The shell adds the native pickers, the
tokens, and the ways in: two for a folder, one for a file.

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
subdirectory named after the folder. Pausing, syncing and stopping a kept
folder are the panel's rows, below.

**A page's Document menu in Gamma's page.** In the desktop app a page's
Document menu has *Follow a file on this computer…* under *Replace the
PDF…* (`frontend/src/app/App.jsx`, shown when `IS_DESKTOP`). The page
posts `{source: "gamma-app", type: "follow-file-on-disk", ws, page}` to its
own origin, and the page preload passes it on as
`shell:follow-file-from-page`, under the folder message's guards. Then it
runs `followFile(wsId, pageId)`:

1. It reads the page's title (`GET /api/blocks/<id>`) for the notices.
2. A page that follows a file already gets a question instead: "“Title”
   follows <path>", with *Reveal* (`revealFile`, the file shown in its
   directory with `shell.showItemInFolder`), *Stop following* (`dropFile`)
   and *Cancel*.
3. Otherwise the native file picker (`pickFile`: one `.pdf`, from the
   Documents folder) is the user's say. Then the link is made where a kept
   folder's would be (below): `POST /api/folder-links {kind: "file", page,
   path}`. On a remote server the shell first mints a write-scope token
   there (a year, named after the page and the file), since each send
   replaces the page's PDF; a link that fails to be made revokes it.
4. The bar says "“Title” follows main.pdf: each new version is sent to
   Gamma.", and the shell offers background mode while it is off.

**Sending PDFs back from a kept folder.** A folder's row in the sync panel
has an upload arrow, a toggle (`push-on`, `push-off`) that runs `setPush`:
`PATCH /api/folder-links/<id> {push}`. It is off until asked for, since
people put annotated copies in a kept folder trusting that nothing goes
back. On a remote folder, kept with a read token, turning it on first
mints a write token on the remote with the window's session and hands it
to the host with the `PATCH` (`token`, `token_id`). The host checks it and
keeps it in place of the read token, which the shell then revokes; a
token the host refuses is revoked instead, and so is one the link the
host answers with does not hold. One change per folder runs at a time: a
second click on the arrow while one runs is ignored, so a double click
never mints two write tokens. Turning it off keeps the write token until
the folder is dropped.

Under the test harness `GAMMA_SHELL_PICK_DIR` answers the directory
picker and `GAMMA_SHELL_PICK_FILE` the file picker, the questions take
their first answer, and a directory "opened" or a file "revealed" is only
recorded (`openDirectory`, `revealFile`, like `openExternal`).

In the Mac App Store build the pick is also the app's access to the
directory or the file, kept as a bookmark (`lib/folderAccess.js`; at
launch a directory is listed and a file checked for reading), and a server
reaches only what the app held when it started. So `keepFolder` and
`followFile` first start the server that is to read or write there again,
on its port, when it started before the pick (`reachFolder`;
[release.md](release.md#mac-app-store) "Folders outside the sandbox").

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
and workspace, kept folders and followed files alike (`listLinks`; the
folder chooser lists only the folders). *Stop* deletes the link on the
host and revokes the token on the remote (`releaseRemote`: `revokeToken`,
with the window's session, which is signed into the remote).
`settings.folderHost` remembers the host so it starts at launch like the
clones' hosts (`startBackgroundHosts`). *Stop* on the host's last remote
link clears it.

The shell keeps nothing else about links; the rounds and the status are
the server's, the host's for a remote folder or page. The bar reports each
action in its status line for a few seconds (`notice` in the shell state).
When a clone, a folder on disk or a followed file is made while
background mode (below) is off, the shell offers it, since all of them
sync only while Gamma runs.

### Kept on this computer

The **sync button** at the bar's right end sums up everything this
computer keeps of Gamma: the clones, the Zotero links, the folders on disk
and the files followed, of every running local server, whichever server
the window shows. It reads *Synced*, *Syncing…*, *Conflicts*, *n
problems*, *Waiting*, *Changes queued* and the like, with a matching icon
and colour, and shows once a server is open or anything is kept; its
tooltip counts them ("1 clone, 2 folders on disk and 1 file followed").
Its **panel** lists four groups, *Clones*, *Zotero links*, *Folders on
this computer* and *Files followed*, each row with its name over its
state and where it is, a state dot, and its round action buttons, always
shown (each named by its tooltip):

- A click on the row opens it: a clone or a Zotero link's workspace in the
  window (`openServer`, then `openGammaWorkspace`), a folder's directory
  (`openDirectory`), a file in its directory (`revealFile`).
- *pause* and *resume* swap with the item's state, and the panel stays
  open on the new one. For a clone they are `POST /api/mirrors/<ws>/detach`
  and `/relink` (the stored token; what both sides did meanwhile merges),
  so a paused clone reads *Detached*, as in Gamma's pill; for a Zotero
  link `POST /api/zotero/links/<ws>/detach` and `/relink`, the same way.
  For a folder or a file they are `pauseFolder`,
  `PATCH /api/folder-links/<id> {paused}`: the server's tick leaves a
  paused link alone, and resuming runs a round.
- *sync* runs a round now, the panel staying open as for *pause*. For a
  clone that is `POST /api/mirrors/<ws>/sync` on its local server, for a
  Zotero link `POST /api/zotero/links/<ws>/sync`, shut while either is
  detached.
  For a folder it is `syncFolder`, which asks first whether to replace
  the files the last round left alone that a forced round would replace
  (the status's kept entries with `force`: files changed on disk, and
  with sending back on the PDFs changed here and in Gamma), and reports
  the round in the bar. A PDF a send could not deliver is no such file:
  Gamma's copy did not change, so there is nothing to replace it with,
  and the row counts it as *n not synced*, the reason in the tooltip. For a file it is
  `sendFile`, a round waited for, and the bar says "main.pdf was sent to
  Gamma.", "nothing new to send", why it is not sent yet, or the problem.
  A paused folder or file syncs too and stays paused.
- A folder's upload arrow turns sending PDFs back on and off (`setPush`,
  above), the panel staying open; it reads pressed while on.
- *stop* (a folder) is `dropFolder`, which asks whether to keep the files
  or take back what the sync wrote. *stop* (a file) is `dropFile`, which
  asks nothing: the file is never touched.

A folder that sends back reads *Up to date · 14:02 · sends back*, with
*n sent* when its last round sent any. A folder's row adds *n changed
here* for the files Sync can replace and *n not synced* for the other
files its last round left alone. A followed file reads, worst first:
*Sending…* (a round runs), *Paused · 14:02*, *Problem* (the reason as the
tooltip), *Waiting for the file to finish* (a file still being written or
not there, drawn as pending, never as an error), *Not sent yet* (no round
yet), *Sent · 14:02*, or *Up to date · 14:02* when its rounds found the file
Gamma's already. When the last send could not move the highlights, the
tooltip says why.

A detached clone or link or a paused folder or file never raises the
button's summary; when everything is, it reads *Paused*. A Zotero link's
PDFs that wait on something outside Gamma (a full storage, files Zotero
does not serve) read *Waiting*, and its changes queued for a later round
*Changes queued*: they only say so, ranked with a first sync not yet run,
below anything that asks for a look. The panel ends with the chooser
above. Conflicts and a clone's or link's cadence stay in Gamma's own sync
pill, in the workspace.

A Zotero link syncs only while its local server runs, so a server that
holds one starts at launch, as the clones' hosts and the folder host do:
`startBackgroundHosts` starts the servers `registry.launchHosts` names,
best effort, a failure logged. The registry records such a server as
`zoteroHost` on its entry (`registry.setZoteroHost`, written only when it
changes). Each reading below sets it while the server's list holds a link
that is not detached, and clears it once its links all went or were
detached; *resume* on a link's row sets it at once. A list that does not
come (a server from before the Zotero links, or one that did not answer)
leaves it as it was. The list is the one the shell's own sign-in, the
server's seeded admin, gets, so a link another account on that server made
does not count. A server the user opens while its launch start is still
under way waits for that start (`startLocal` runs one start per server at
a time), so two processes never race for its port and data directory
(`test/main.test.js` and `test/registry.test.js`, `npm test`).

`refreshKeeping` reads every running local server's `GET /api/mirrors`,
`GET /api/zotero/links` (none from a server that has no such list) and
`GET /api/folder-links` every 15 s, every 2 s while something syncs, when
the panel opens and after each action. A call while a reading runs gets the
reading that starts once that one ends, and the running reading's result is
dropped: an action calls it right after changing something on a server,
which that reading may have read before the change. A timer that comes due
during a reading leaves it be. `lib/keeping.js` turns the rows into
items and the button's summary, with no Electron in it
(`test/keeping.test.js`, `npm test`). A clone and a Zotero link read as
Gamma's sync pill reads them (`mirrorState` in
`frontend/src/collaboration/MirrorPopover.jsx`, a link's errors by
`status.error_kind` and its waiting files as
`frontend/src/collaboration/syncLinks.js` reads them,
[docs/dev/zotero_sync.md](../../docs/dev/zotero_sync.md#frontend)):
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
reachable dot, a running local one's address, check on the current one),
then *All servers…* (the launcher again). At the right: the sync button and its panel (above, "Kept
on this computer"), the update pill (only while an update is ready, see
below) and a reload button. One dropdown is open at a time. While one is,
the bar view is temporarily enlarged over the content (its page is
transparent outside the strip and the dropdown), which is how a 38 px view
can show a menu. A press outside the dropdown and its button closes it
(a `pointerdown`, not a click: iOS sends no click for a tap on the bare
see-through page), as do Escape and the window losing focus.

The **content view** shows the launcher or the server. The launcher lists
servers as cards (kind, running / reachable dot, size on disk, a running
local server's address, data dir / URL, *last opened* badge) with open /
rename / copy address / credentials / data folder / server log / remove
actions, then Settings: the *reopen last server at
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
light machine never opens a dark window. The launcher's first paint takes
that theme from its `?theme=` (`loadLauncher`) and shows only the page's
colour until its first server list arrives, so leaving a server for it
does not flash a light, empty page; the content view's background follows
the theme too, for the moment between one page and the next.

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

**The iPad app shows these pages too.** It copies `ui/` into its bundle
and shows `bar.html` above its web view and `launcher.html` over it, with
a `gammaShell` of its own in Swift ([docs/dev/ipad.md "The chrome"](../../docs/dev/ipad.md#the-chrome)).
A change to the bar or the launcher is a change to both shells. Its
`gammaShell.platform` is `ipad`, which each page puts on
`body[data-platform]`. What only the desktop does is hidden there in two
ways, neither of which changes the desktop. Its state has no `keeping`,
`update` or `settings`, so the bar shows no sync button, *clone* chip or
update pill, and the launcher no Settings block. And one rule in
`theme.css`, `body[data-platform="ipad"] .desktopOnly`, hides *New local
server* and a local card's Remove. Under the same attribute `theme.css`
and `bar.html` take touch sizes: a 44 px bar below the status bar's inset,
36 px targets, no 146 px reserve. The bar's status stays on one line, and
the shortcut hints read ⌘⇧L and ⌘R. One control is the iPad's own: a
chevron after reload that hides the bar (`gammaShell.hideBar`), shown only
while the state's `barHideable` is true, a key the desktop's state never
has.

### Gamma Cloud sign-in

A server's *Sign in with Gamma Cloud* (its login page) and *Link* (Settings
→ Account & sync) navigate to `/api/auth/cloud/start`. The navigation guard
would send the account server's page to the system browser, and the
session the server sets at the end would stay there. So the guard takes
that navigation over on a registered origin (`signInWithCloud` in `main.js`,
`lib/cloudSignIn.js`):

1. It listens on a free `127.0.0.1` port, posts `/api/auth/cloud/app-start`
   through the content session with the page's `next` and `link`, that
   address and the S256 challenge of a verifier it keeps, and opens the
   authorize URL it gets back in the system browser. The bar says to finish
   there.
2. After the sign-in, the server sends that browser to
   `http://127.0.0.1:<port>/cloud-signin?result=…`. The tab says "Signed in
   to Gamma", the listener closes, and the window comes to the front.
3. The content view posts the result and the verifier to
   `/api/auth/cloud/app-claim` (with `X-Gamma-Desktop: claim`), which sets
   the session cookie in the window and goes on to `next`.

A refusal comes back as `?error=` and is shown in the bar and in the tab. A
sign-in waits ten minutes, and a new one replaces it. A server older than
`app-start` (it answers 404 or 405) gets the old behaviour: the start
opens in the system browser, which ends up signed in instead of the
window. A result for a server that is no longer open is dropped with a
notice. The server side, and why
this is safe against someone else's sign-in link, is
[cloud_accounts.md](../../docs/dev/cloud_accounts.md) "The desktop app's
sign-in".

### Opening PDF files

Every build registers Gamma as a PDF app. It is listed under *Open with*,
and the user can make it the default. No build makes itself the default;
the user picks it: *Open with → Choose another app → Always* or Settings →
Default apps on Windows, *Get Info → Open with → Change All* on macOS, the
desktop's default-applications setting on Linux. The registration differs
per target (`electron-builder.cjs`):

- The **NSIS installer** runs `assets/installer.nsh`. It writes the ProgID
  `Gamma.pdf` (open command `"Gamma.exe" "%1"`), adds it to `.pdf`'s
  `OpenWithProgids`, and registers Gamma's capabilities under
  `RegisteredApplications`, which puts Gamma in Default apps. It never
  writes the `.pdf` key's default value. electron-builder's own
  `fileAssociations` does, and its uninstaller then leaves `.pdf` pointing
  at a ProgID that is gone, so the config has no top-level
  `fileAssociations`. The keys go under HKCU or HKLM as the install did;
  the uninstaller removes them and an update keeps them, so Gamma stays
  the default where the user made it so.
- The **Store package** adds a `windows.fileTypeAssociation` extension from
  `assets/appx-extensions.xml` (`appx.customExtensionsPath`).
- **macOS** declares the type in `Info.plist` with `LSHandlerRank`
  *Alternate* (`mac.fileAssociations`).
- The **.deb**'s desktop entry lists `application/pdf` (`linux.mimeTypes`).
  A `fileAssociations` entry with a MIME type would also install its own
  definition of `application/pdf`.

A file arrives on the command line (Windows, Linux) or as an `open-file`
event (macOS, before `ready` at a launch too). The app runs once per
profile (`app.requestSingleInstanceLock`, keyed on the userData dir): a
second start hands its command line to the running app (`second-instance`)
and quits, and without a PDF it only brings the window forward. A second
app would run a second server on the same data directories.
`lib/openFiles.js` reads the arguments. It skips switches (Chromium adds
its own to a second start's), reads a `file://` URL as its path, and keeps
existing files ending in `.pdf`. A start that names a file never goes
hidden into the tray.

Each file is added as an upload in Gamma adds it, one at a time
(`openPdfFiles` → `addPdf` in `main.js`), through the content session:

1. The window comes forward. With no server open, the last one opens, else
   the first local server, made when there is none.
2. `GET /api/session` gives the workspace (`openFiles.placeFor`): the one
   the window shows (`?ws=`, else the account's default) when the account
   can write there, else its personal one, and the bar says why. A
   workspace others see asks first: one with other members, a public one,
   or a clone, whose changes reach its origin. The question offers *Add to
   <workspace>*, *Add to <personal workspace>* and *Cancel*. Signed out, or
   with nowhere to write, a dialog says so.
3. `POST /api/uploads` stores the file, read from disk as it goes; past
   32 MB it goes in parts (`/api/uploads/parts`, as
   `frontend/src/shared/lib/uploadParts.js` sends them).
4. `POST /api/blocks/by-doc/<hash>` finds the page carrying the file or
   makes one titled with the file's name. A file already in the library
   opens its page, not a second one.
5. `POST /api/import/pdf-annotations` turns the annotations another reader
   saved in the file into Gamma's. They are stripped from the stored copy
   unless the account set *Embedded PDF annotations* to hide them
   (`embAnnots` in `GET /api/prefs/profile`, the one preference name the
   shell reads). A failure here leaves the page as it is.
6. The window loads `<origin>/?ws=<id>&page=<id>`.

Under the test harness the question takes its first answer, and
`openPdfFiles` is on the test hook.

### The microphone

A server's page records into a note and dictates to the assistant with
`getUserMedia`. Electron grants a page every permission it asks for unless
the session has a handler, and the shell's (`lib/microphone.js`, set on the
default session at launch) still grants everything, with one exception:
the microphone on macOS, which the system lets an app use only once the
user said yes. When the system has not asked yet, the handler has it ask
(`systemPreferences.askForMediaAccess`), with the app's
`NSMicrophoneUsageDescription` (`mac.extendInfo` in `electron-builder.cjs`),
and the page gets the user's answer. When the user said no, or the Mac
does not allow it, the page is refused and reports that it could not
record, and a dialog says where to allow Gamma: System Settings → Privacy &
Security → Microphone, with a button that opens it. The answer reaches
Gamma at its next start, which macOS also says. A build signed with the
hardened runtime (the dmg with a Developer ID) or the App Sandbox (the
Mac App Store build) also needs `com.apple.security.device.audio-input`,
which both entitlement files carry; an ad-hoc signed build has neither and
needs only the purpose string. Windows and Linux leave the microphone to
the page and the system's own settings.

### Background and tray

Closing the last window quits the app (macOS keeps the dock process, as
any Mac app) unless **Keep running in the background** is on. The setting
is `settings.background`, turned on by the launcher's switch, the *Server*
menu, the tray menu, or `offerBackground`, the offer made when a clone, a
folder on disk or a followed file is first created. On, `window-all-closed` leaves the app
running with a tray icon (`ensureTray`: *Open Gamma*, the two switches,
*Quit Gamma*). At launch `startBackgroundHosts` then starts every local
server instead of only the hosts of clones, of remote folders and files and
of Zotero links, so clones, Zotero links, folders on disk and followed files
keep syncing with no window. The tray's click or *Open Gamma* brings the
window back where the user left off (`showWindow`). Turning the setting off
from the tray with no window quits.

**Start at login** (`settings.openAtLogin`) registers the OS login item
with `--hidden`, so a login start makes no window and goes straight to the
tray (`applyLoginItem`; macOS reports a hidden start itself through
`wasOpenedAsHidden`). On Windows and macOS that is `app.setLoginItemSettings`;
on Linux an autostart entry under `~/.config/autostart`. Packaged builds
only, and nothing under the test harness. The Mac App Store build has no
login item (a sandboxed app needs a login helper of its own, which it does
not have): the launcher and the menus leave the switch out.

## In-app updates

`lib/updater.js` wraps `electron-updater`, VS Code style: a silent check
15 s after launch and every 4 h, download in the background, then one
attention-seeking control — the **Restart to update** pill in the shell bar
(and the same action in the launcher's Updates row). *Help → Check for
Updates…* is the only flow that answers with a dialog. The feed is the
GitHub Release (details and the signing caveat: [release.md](release.md)).

State machine (`update` in the shell state): `idle` → `checking` →
`up-to-date` | `downloading` (percent) → `downloaded` | `error`;
`unsupported` in dev builds, under the test harness, in a Microsoft
Store install (`process.windowsStore`; the Store delivers those updates,
see [release.md](release.md#microsoft-store)) and in the Mac App Store
build (`process.mas`, reason `mas`: App Review allows no other way to
update, so it never even reads the feed, [release.md](release.md#mac-app-store)). On **Windows** the
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
which the Store uninstall deletes — [release.md](release.md#microsoft-store);
the launcher shows that real location, and opens it, through `lib/hostPath.js`;
the Mac App Store build's is in its container,
`~/Library/Containers/net.blitzbuild.gamma/Data/Library/Application Support/gamma-desktop`):

- `servers.json` — the registry: server list (a local server with its
  admin credentials, last `port`, and `zoteroHost` while it holds a Zotero
  link that is not detached, "Kept on this computer"), `lastOpened`,
  `windowBounds`,
  and `settings` (`openLastOnLaunch`, `lastTheme`, `dataRoot`, `background`
  and `openAtLogin` (the tray, above), `folderHost` (the local server that
  keeps folders and files of remote servers), the dev-mode
  `pythonPath`/`backendDir`/`staticDir` overrides). Local admin credentials
  are stored in plaintext here — same trust level as the SQLite files next
  to it; acceptable for a per-OS-user desktop app. The Mac App Store build
  also keeps `access` here: the folders and followed files outside its
  sandbox the user picked, as security-scoped bookmarks
  (`lib/folderAccess.js`). An older profile's
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
each `<id>` dir a file at a time and asynchronously (`copyTree`, so it works
across drives and the app stays live), and only after every copy succeeded
re-points the entries and deletes the originals. Meanwhile the launcher
shows the bytes copied of the total (`shell:data-move`, a bar under *Local
server storage* and the status line), and the servers being moved refuse to
open. A
failure midway rolls back the copies and leaves the registry untouched. The
new folder may not be inside the current one or contain it. Removing a
server offers *keep files* / *delete everything*; deletion is guarded to
directories under the default or the configured root only.

`GAMMA_SHELL_USER_DATA=<dir>` relocates all of it (the tests use a temp
profile); `GAMMA_SHELL_DOWNLOAD_DIR=<dir>` saves downloads there without the
dialog, `GAMMA_SHELL_PICK_DIR=<dir>` answers the folder picker and
`GAMMA_SHELL_PICK_FILE=<file>` the file picker (tests only); `GAMMA_SHELL_NO_UPDATE=1` disables the updater.

## File map

- `main.js` — window + views + layout, theme mirror, native menu
  (accelerators only; hidden behind Alt on Windows; *Help* holds *Check for
  Updates…*), IPC for the shell pages, auto-login script, navigation guard
  (only registered server origins may load in the content view; everything
  else — `target=_blank`, cross-origin redirects — opens in the system
  browser; a server's Gamma Cloud sign-in start is taken over, "Gamma Cloud
  sign-in"), the remote health probes, the `/api/session` read behind the
  workspace switcher, the clone, folder-on-this-computer and
  file-on-this-computer flows over the server's API, the tray and background mode, one app per profile and the
  PDFs the system hands it ("Opening PDF files"), `--smoke` self-test,
  sidecar cleanup on quit, the `GAMMA_SHELL_TEST` hook the e2e suite drives.
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
  1.6 px stroke, and section labels in Gamma's sentence case; and the
  iPad's rules (`.desktopOnly`, touch sizes; "Window").
- `ui/bar.html` — the shell bar. `ui/launcher.html` — the server picker.
  Both plain HTML, no build step, and the iPad app's chrome as well.
- `lib/registry.js` — `servers.json` load/save, add/rename/remove,
  last-opened, settings, window bounds, data-dir size, the local servers
  started at launch (`launchHosts`); no Electron, unit tested by
  `test/registry.test.js`.
- `lib/sidecar.js` — local server lifecycle: the port (`pickPort`, "A local
  server's address"; `test/sidecar.test.js`), spawn, health poll
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
- `lib/cloudSignIn.js` — the loopback return of a Gamma Cloud sign-in: the
  listener, the verifier and its challenge, the tab's last page, the claim
  request ("Gamma Cloud sign-in"); no Electron, unit tested by
  `test/cloudSignIn.test.js`.
- `lib/openFiles.js` — the PDFs a command line names and the workspace a
  file goes into ("Opening PDF files"); no Electron, unit tested by
  `test/openFiles.test.js`.
- `lib/hostPath.js` — a path as programs outside a Store package find it:
  the package's private copy of AppData ("Shell state"); no Electron, unit
  tested by `test/hostPath.test.js`.
- `lib/folderAccess.js` — the Mac App Store build's folders and followed
  files outside its sandbox: the bookmarks kept, the ones opened at
  launch, and when a
  server must start again to reach one ([release.md](release.md#mac-app-store));
  no Electron, unit tested by `test/folderAccess.test.js`.
- `lib/microphone.js` — the pages' permission requests: all granted, the
  microphone on macOS once the system allows it ("The microphone"); no
  Electron, unit tested by `test/microphone.test.js`.
- `electron-builder.cjs` — the packaging config (targets, extra resources,
  secret-gated signing, the update feed's `publish` block, the PDF
  registration per target). `assets/installer.nsh` — the NSIS installer's
  PDF registration; `assets/appx-extensions.xml` — the Store package's.
  `scripts/adhoc-sign.cjs` — its `afterPack` hook: ad-hoc signs a macOS
  build made without a certificate ([release.md](release.md)).
- `electron-builder.mas.cjs` — the Mac App Store build, extending the
  config above (bundle id, versions from the environment, the sandbox
  entitlements `assets/entitlements.mas.plist` and
  `assets/entitlements.mas.inherit.plist`, the profile).
  `scripts/mas-modes.cjs` — its `afterPack` hook: file modes the installed
  app can read ([release.md](release.md#mac-app-store)).
- `backend_entry.py` — entry for the frozen server (`--port`, `--data-dir`;
  sets env before importing gamma, serves the bundled `frontend_dist`).
- `build_backend.py` — PyInstaller onedir freeze into
  `dist-backend/gamma-server/` (collects uvicorn's string-resolved modules,
  PyMuPDF's native libraries — MuPDF and its C++ wrapper beside the
  extension module, `--collect-binaries pymupdf`, about 40 MB of the bundle —
  ziamath/ziafont fonts, and `frontend/dist`). certifi's CA bundle comes
  along through its PyInstaller hook: on a Mac the frozen server's OpenSSL
  looks for CAs under the build machine's python.org framework, so the
  server verifies HTTPS with certifi's instead ([api.md](../../docs/dev/api.md)).
  Handles conda-based interpreters by adding `<base>/Library/bin` to the
  DLL search path.
- `assets/icon.png` — app icon (1024 px, the favicon mark, from the brand
  generator; electron-builder derives ico/icns and the Linux icon set, and
  App Store Connect wants the icns's 1024 px image). `assets/entitlements.mac.plist` —
  hardened-runtime entitlements for signed mac builds, the microphone
  among them ("The microphone").
- `assets/appx/` and `assets/store/` — tracked Store package/listing images;
  `scripts/store-art.js` regenerates them. The packager's build-resources
  directory is `assets/`; `build/` holds generated PyInstaller intermediates.
- `test/main.test.js` — `main.js` under plain Node, Electron replaced by a
  stand-in and the local servers by fakes (the `GAMMA_SHELL_TEST` hook):
  the launch record a keeping reading writes, a resume made during a
  reading, one start per server (`npm test`).
- `test/e2e.js` — the Playwright-driven end-to-end suite (see
  [checklist.md](checklist.md)). `test/smoke.js` — runs the app's `--smoke`
  self-test (dev or `--packaged`).

## Invariants

- The shell must keep treating Gamma as a black box: talk to it only via the
  public HTTP API + env config (`GAMMA_DATA_DIR`, `GAMMA_STATIC_DIR`,
  `GAMMA_ADMIN_USER`, `GAMMA_ADMIN_PASSWORD`, `GAMMA_VERSION` = the shell's
  own version, so the server's admin dashboard names the app,
  `GAMMA_UPDATES` = `desktop`, or `store` in a Microsoft Store install, or
  `mas` in the Mac App Store build (which also sets `GAMMA_UPDATE_CHECK=off`),
  so it says who installs a newer one,
  `GAMMA_CLOUD_DEFAULT_ISSUER` = Gamma Cloud, the account server until the
  admin saves another, `GAMMA_FOLDER_LINKS` = this server keeps folders and
  files on disk, anywhere the user picks, since the sidecar runs as the user), `/api/health` and
  `/api/session` (+ the `?ws=` and `?page=` URL parameters). No imports from `backend/`,
  no frontend patches. The one thing it reads off the page is the
  `data-theme` attribute (read-only, via the preload).
- Server pages never get an IPC bridge; `gammaShell` exists only on the
  shell's own `file:` pages, and every handler re-checks the sender.
- `ui/` is also the iPad app's chrome ("Window"). A page keeps working
  under the iPad's `gammaShell`, which has only the calls the iPad answers
  and no `keeping`, `update` or `settings` in its state: a control only
  the desktop answers either reads one of those keys or carries
  `.desktopOnly`. The one only the iPad answers, the bar's hide chevron,
  reads `barHideable`, which only the iPad's state has.
- Local sidecars bind `127.0.0.1` only (the LAN-exposed use case is the
  existing server/Docker deployment, not the desktop app).
- Navigation allowlist: only registered server origins render in the
  content view; foreign URLs (including `window.open` and cross-origin
  redirects) go to the system browser. A Gamma Cloud sign-in runs there
  too, and its session comes back only through a loopback address and a
  verifier the shell holds ("Gamma Cloud sign-in").
- The updater never installs without the user's click (the pill / button /
  dialog); automatic work is limited to checking and downloading.
