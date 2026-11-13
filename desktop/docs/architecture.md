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
  after navigation by POSTing `/api/login` from the page, then loads the
  page again itself (`signedInAgain`, through `loadPage`) and waits for it,
  so an open answers with the signed-in page there and the next load (a
  workspace, a page) never starts under that reload. The window holds one
  `session` cookie per host, so a switch between two local servers signs in
  again each time. Several local
  servers are still possible (one data dir and process each; a sidecar keeps
  running until the app quits, so switching back is instant, and a window
  opened again shows the same process), but the usual shape is one local
  server with several workspaces.
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

### Synced vaults

A **synced vault** keeps something in Gamma and something on this computer
in step: a folder and a directory (each page a Markdown note, each paper
its PDF beside it), or a page and one PDF file. The words are git's, and
the shell speaks them everywhere (`lib/keeping.js`): Gamma's server is the
*remote*, the directory or the file is *local*, **pull** writes Gamma's
changes to the disk, **push** brings the disk's into Gamma (*off*, *PDFs*
or *everything*), and **sync** is push, then pull. Only the app's own local
servers keep vaults (started with `GAMMA_FOLDER_LINKS=1`); the rounds, the
marker, the status and the rules are theirs
([docs/dev/synced_vaults.md](../../docs/dev/synced_vaults.md)). A NAS keeps
none: it answers the reads, the ops and the uploads a vault of its folder
sends it, and a vault of its folder waits, its reason in its status, while
the NAS keeps its pages in another shape than the app's own server (the
change feed's workspace data format, as a clone pauses): update the NAS
with the app. The shell adds the native pickers, the dialog, the tokens, the
word that lets a path through, and the ways in:

| Where | What the user picks | Flow |
|---|---|---|
| a library folder's right-click *Sync with this computer…* (`frontend/src/app/App.jsx`) | a directory | `addFolderVault(ws, folder)` |
| the sync panel's *Sync a folder of <workspace> with this computer…* (below, "The sync panel") | a folder, then a directory | `addFolderVault(ws, folder)` |
| *Add a synced vault…* in the launcher's buttons and at the panel's end | a directory | `addDirectoryVault()` |
| a page's Document menu, *Push from a file on this computer…* | a PDF | `addFileVault(ws, page)` |

**A page's asks.** Gamma's page never names a path. It posts `{source:
"gamma-app", type, ws, folder | page}` to its own origin, the way it greets
the Connector extension, and the page preload passes the message on as it
is (`PAGE_ASKS` in `preload.js`) and exposes nothing:

| Message | Channel | Runs |
|---|---|---|
| `keep-folder-on-disk {ws, folder}` (*Sync with this computer…*) | `shell:sync-folder-from-page` | `addFolderVault` |
| `follow-file-on-disk {ws, page}` (*Push from a file on this computer…*) | `shell:push-file-from-page` | `addFileVault` |
| `reveal-on-disk {ws, page}` (the vault chip's *Show in folder*) | `shell:reveal-from-page` | `revealOnDisk` |
| `relocate-file {ws, page}` (the banner's *Locate…*) | `shell:relocate-from-page` | `relocateFile` |
| `carry-to-server {ws, page}` (*Add to library…*'s *Another server…*) | `shell:carry-from-page` | `carryToServer` ("Opening PDF files") |

The first two keep the names they had before synced vaults, so that a
desktop app released before them, which passes on only those, still
forwards them from an updated server's page. `sync-folder-on-disk` and
`push-file-on-disk` reach the same channels as spare names; no page sends
them. The other three are new: an older app ignores them. The shell takes a message only from the content
view's page of the open server (its frame's origin), with plain ids
(`[\w-]{1,64}`), and only while nothing else runs: no action in the bar,
no vault dialog up, no other ask (`fromPage`). It takes at most one a
second (`PAGE_ASK_MS`), so a page asking in a loop opens no row of file
manager windows. Each runs under `oneAsk`, as `shell:add-vault` and
`shell:add-folder-vault` do: one flow that asks the user (a picker, a
native question, the dialog) at a time, held from its first line until it
settles, a second one dropped. A failure is an error box. The picker is
the user's say, so a page can start nothing on its own. `revealOnDisk`
answers only on a local server: a remote's page has no chip, and a page's
ask never starts a server (`pageOnDisk` reads the host's links only while
the host runs).

**A folder's vault** (`addFolderVault`):

1. It reads the workspace's folders (`GET /api/sync/folders`). A folder
   that has a vault already opens its directory instead and says so.
2. The native picker (`pickDirectory`) asks for the directory, which is the
   vault as it is, empty or not; the dialog says what is in it first. The
   window must still show the server the ask came from (`stillOn`): one
   opened while the picker was up has its own session.
3. The server that keeps the vault is the open one when it is local, and
   the **host** for a remote server's folder (below). It previews the
   directory (`POST /api/folder-links/preview`), which writes nothing and
   answers what it found, the marker it holds, the folder's pages and any
   refusal. For a remote folder the host cannot count the pages, so the
   shell reads them from the remote (`GET /api/sync/folders/<id>`).
4. An empty folder of a local server synced with a directory of notes or
   PDFs would push everything (`firstPush`): it becomes a **work folder**
   (below), and the bar asks nothing. Any other asks the dialog (below),
   which starts with Pull on and Push off: a folder with pages, a
   directory with no notes or PDFs, a remote's folder, the whole library.
5. On *Sync*: on a local server `POST /api/folder-links {folder, path,
   notes, pull, push, adopt}` in the folder's workspace. `adopt` takes up
   the marker of a vault no link holds any more, which the preview reported.
   For a remote's folder the shell first mints a token there (`POST
   /api/integrations/tokens`, a year, named after the folder), read scope
   when the vault pushes nothing and write scope otherwise, and makes the
   vault on the host with `remote_url`, `workspace`, `token` and
   `token_id`; a vault that fails to be made revokes the token.

**A directory's vault** (`addDirectoryVault`): the picked directory
becomes a work folder (below) in a new folder named after it
(`new_folder`). It needs no open server. It lands (`vaultPlace`,
`openFiles.readerPlace`) on the open local server, through the window's
session, in the workspace the window shows when the account writes there,
it is no clone and no one else sees it, or else in the account's own
workspace there; otherwise on the host, started and signed into in its own
session, in the account's personal workspace. A shared workspace takes a
directory through its folder's *Sync with this computer…*, which names it.

**A work folder** (`makeWorkFolder`; [docs/dev/work_folders.md](../../docs/dev/work_folders.md))
is a vault of a local server's folder that pushes everything, whose files
the person chooses in the web app's tracking dialog. The shell asks nothing
of its own: a refusal the preview tells (a marker another vault holds) is
said in the bar and nothing is made; else it posts `POST /api/folder-links
{path, new_folder | folder, pull: true, push: "everything", choose: true,
adopt}` with the shell's word, which makes the vault paused and *choosing*,
its marker written and no round run. Then it hands over: the server's window
(opened when another shows) goes to the vault's folder with its dialog,
`openGammaWorkspace(ws, {folder, track: <link id>})`, which loads
`?ws=…&folder=…&track=…`. That is all the shell gives the page: the dialog
lists, previews and saves through the server's routes, which take the link
by its id and never a path, so still nothing passes from the shell to the
page. Its Save writes the `.gammaignore` and runs the first round; its
Cancel removes the vault and what making it made. The panel's *Choose
files…* (`choose`) navigates the same way for a work folder there is.

**A file's vault** (`addFileVault`) pushes, never pulls: each new version
of the file becomes the page's PDF, as *Replace the PDF…* makes it, and the
file is never written. A page that has one already asks instead (*Show in
folder*, *Remove the vault…*, *Cancel*). Otherwise the picker (`pickPdf`)
is the user's say, and the vault is `POST /api/folder-links {kind: "file",
page, path}`, on the host with a write token minted on the remote for a
remote's page. On a local server the one hint about vaults of one file
comes first (`oneByOne`): when two or more vaults of one file of the
workspace already sit in the picked file's directory, and a directory's
vault there would land in that very workspace, it asks "3 files in
D:\Papers are synced one by one. Sync the folder instead?" *Sync the
folder…* runs `addDirectoryVault` on that directory with no picker, and
the new work folder takes the files' pages in when its dialog saves;
*Push this file only* goes on.

Each flow ends with a notice in the bar, offers background mode while it
is off (all of them sync only while Gamma runs), and reads the panel again;
a work folder's ends on its tracking dialog instead of a notice.

**The vault dialog** is a view of the sync panel in `ui/bar.html`, as the
folder chooser is, not a native dialog (`askVault`, the shell state's
`vaultAsk`). It shows *Local* (the path), *Remote* (server · workspace ·
folder, or "a new folder “name”"), *Found* (the preview's notes, PDFs,
pictures and untracked files, or an empty directory), the switch **Pull**,
**Push** as *off / PDFs / everything* (*everything* shut for a remote's
folder and for the whole library, which starts pulling only), one sentence
saying what the first round does, and *Cancel* and *Sync*. The sentences
are `keeping.firstRound`'s, made in `main.js` for each setting of the two
switches before the dialog shows. One that is not `ok` says why and shuts
*Sync*: both switches off, a remote's folder asked to push everything or
not to pull, the whole library asked to push everything
(`keeping.WHOLE_RULE`: a work folder needs a folder of its own), the
preview's refusal, a folder with
pages and a directory with files that would push everything into each
other. `answerVault` takes only an answer whose sentence is `ok`. While the
dialog is up the bar stays expanded on it (`setBarExpanded`), so a look at
the page meanwhile does not hide it, and the window losing the focus leaves
it. Closing the panel (Escape, a press outside it, the sync button) is
*Cancel*, and so is the window going to the launcher or to another server
(`cancelVaultAsk`): it was asked about what the window showed. A newer
dialog cancels the one shown, and a state that still holds a dialog opens
the panel on it again (`takeState`).

**The shell's word.** The routes that take a path on this computer answer
only a request that carries `X-Gamma-Shell` with the secret the sidecar
was started with: `POST /api/folder-links`, `/preview`, `/<id>/relink`,
`/open` and `/opened/<id>/locate`. The shell makes the secret at each start
(`SHELL_SECRET`, 32 random bytes) and hands it to every local server as
`GAMMA_SHELL_SECRET` (`lib/sidecar.js`), so a page of Gamma, which shares
its server's origin and session, cannot name a path itself. `shellWord(origin)`
gives the header only for the origin of a running local sidecar of the
registry and throws for any other, so neither the word nor a path reaches
a remote server, whatever the window opened while a picker was up. A flow
records the server it was asked of when it starts and takes that server's
origin from its sidecar (`localOrigin`), never from what the window shows
by then.

**A remote server's folder** needs no clone. The vault lives on the
**host**, the first local server, which `ensureHost` makes, starts and
signs into when needed (the clones' host too), and reads the folder over
HTTP with the token: only that folder's files come down. From the shell it
pulls, and pushes PDFs at most for now. The vaults of a remote workspace
are the host's links with that origin and workspace (`listLinks`; the
chooser lists only the folders'). `settings.folderHost` remembers the host
so it starts at launch like the clones' hosts (`startBackgroundHosts`), and
removing the host's last vault of a remote server clears it. Turning push
on for such a vault, made with a read token, first mints a write token on
the remote with the window's session and hands it to the host with the
`PATCH` (`token`, `token_id`). The host checks it and keeps it in place of
the read token, which the shell then revokes; a token the host refuses is
revoked instead, and so is one the vault the host answers with does not
hold. One change of push per vault runs at a time (`setPush`): a second
click while one runs is ignored, so a double click never mints two write
tokens. Turning push off keeps the write token until the vault is removed,
which revokes it (`releaseRemote`, with the window's session, signed into
the remote).

In the Mac App Store build the pick is also the app's access to the
directory or the file, kept as a bookmark (`lib/folderAccess.js`; at
launch a directory is listed and a file checked for reading), and a server
reaches only what the app held when it started. So each flow that picks a
path, *Locate…* included, first starts the server that is to read or write
there again, on its port, when it started before the pick (`reachFolder`;
[release.md](release.md#mac-app-store) "Folders outside the sandbox").

Under the test harness `GAMMA_SHELL_PICK_DIR` answers the directory
picker and `GAMMA_SHELL_PICK_FILE` the file picker. A native question
takes the answer a test queued (`answers` on the test hook), else its
first one, and is recorded with its detail (`asked`); a directory "opened"
or a file "revealed" is only recorded (`openDirectory`, `revealFile`, like
`openExternal`). The launcher's *Open data folder*, *Show server log* and
*Show log file* go through the same two helpers.

The shell keeps nothing else about vaults: the rounds and the status are
the server's, the host's for a remote's folder or page. The bar reports
each action in its status line for a few seconds (`notice` in the shell
state).

**The focus hint.** A window that gets the focus posts `POST
/api/folder-links/hint` to each running local server (`hintServers`, at
most every 10 s, `HINT_MS`, best effort): the server's tick runs at once in
the background, so a vault looks at its disk when the user comes back from
another app rather than at the next 30 s tick.

### The sync panel

The **sync button** at the bar's right end sums up everything this
computer keeps of Gamma: the clones, the Zotero links and the synced
vaults of every running local server, whichever server the window shows.
It reads *Synced*, *Syncing…*, *Conflicts*, *Held*, *n problems*, *Not
synced yet*, *Starting…*, *Waiting*, *Changes queued* and the like, with a
matching icon and colour, and shows once a server is open, anything is
kept or the vault dialog is up; its tooltip counts them ("1 clone and 2
synced vaults on this computer: synced"). Its **panel** lists three
groups, *Clones*, *Zotero links* and *Synced vaults*, each row with its
name over its state, a state dot, and its action buttons, always shown
(each named by its tooltip). A vault's row adds its direction after its
name as the two arrows its view's *Pull now* and *Push now* carry: the
pull arrow lit while it pulls, the push arrow lit while it pushes
everything and half-lit while it pushes PDFs only, dim while off (a vault
of one file shows the push arrow alone). Its `path ⇄ remote · folder`,
`D:\Papers ⇄ NAS · Research / Papers`, is in the row's tooltip. Under
the groups come *Add a synced vault…* and, while a
workspace is open, *Sync a folder of <workspace>…*. That opens the
panel's second level, the chooser: *Whole library* and every folder of the open workspace
(`shell:folders` → `listFolders`: the open server's `GET /api/sync/folders`
with the content session's cookies, and the vaults from `listLinks`). A
folder that has a vault reads *synced* and opens its directory on click
(`shell:open-path`, only for a directory one of those vaults names); any
other asks for a directory (`shell:add-folder-vault` → `addFolderVault`).

- A click on the row opens it: a clone or a Zotero link's workspace in the
  window (`openServer`, then `openGammaWorkspace`), a vault's directory
  (`openDirectory`) or its file in its directory (`revealFile`).
- *pause* and *resume* swap with the item's state, and the panel stays
  open on the new one. For a clone they are `POST /api/mirrors/<ws>/detach`
  and `/relink` (the stored token; what both sides did meanwhile merges),
  so a paused clone reads *Detached*, as in Gamma's pill; for a Zotero
  link `POST /api/zotero/links/<ws>/detach` and `/relink`, the same way.
  For a vault they are `PATCH /api/folder-links/<id> {paused}`: the
  server's tick leaves a paused vault alone, and resuming runs a round.
- *sync* runs a round now, the panel staying open as for *pause*. For a
  clone that is `POST /api/mirrors/<ws>/sync` on its local server, for a
  Zotero link `POST /api/zotero/links/<ws>/sync`, shut while either is
  detached. For a vault it is *Sync now: push, then pull* (*Push now* for
  a vault of one file), `POST /api/folder-links/<id>/sync?direction=both`,
  whose outcome the bar says (`keeping.roundSaid`). Every round a vault's
  action asks for goes through `roundOf` and `followRound`: the `POST`
  answers at once with the round's `running` set (the one it started, or
  one already running, after which the server runs the round asked for),
  and the link is read every second until `running`
  comes down, however long the round takes (the server reads `running`
  from the round's lock, so a mark a stopped server left never reads as a
  round), and no request stays open for it. A paused vault syncs too and
  stays paused.
- *more* (…) opens the vault's own view, the panel's second level, where
  the switches are. A row carries none of its own: a push switch there
  could drop a vault that pushes everything to off in one click.

**A vault's own view** has a back row (*Synced vaults*), *Local*, *Remote*
and *State* (the state's tooltip on it), and for a folder's vault the two
switches as the dialog has them: a remote's folder keeps *Pull* on and
*everything* shut (`keeping.REMOTE_RULE`, which `setPull` and `setPush`
refuse too). A work folder adds the switch *Add its PDFs and notes to the
library* (`setLibrary`, `PATCH {library}`): whether the files it finds from
now on are library documents too; the files it holds keep theirs. Its
everyday actions follow as a bar of round buttons
(`.vaultBar`: *Sync now*, *Pull now*, *Push now*, *Pause* or *Resume*, and
the directory), each named by its tooltip; the rare and the grave ones
keep their rows, where their words warn. Each goes through
`shell:keeping-action` (kind, server, id, action, value: `VAULT_ACTIONS` in
`main.js`):

| Action | Shown | Does |
|---|---|---|
| *Sync now* (*Push now* for a file) | but while a work folder chooses its files | `POST …/sync?direction=both`, followed to its end |
| *Pull now*, *Push now* | a folder's vault; shut while that switch is off | `…?direction=pull`, `…?direction=push` |
| *Pause*, *Resume* | always; no *Resume* while a work folder chooses its files | `PATCH {paused}` |
| *Choose files…* | a work folder | its tracking dialog in the window (`chooseFiles`, as a work folder's making hands over) |
| *Go on…* | a vault held by its guard | the question `keeping.goOnAsk`, then `…?confirm=1` |
| *Locate…* (*Locate the file…*) | a stopped vault or one whose directory is not there; a file's vault always | the picker, then `POST …/relink {path}` with the shell's word; the directory's marker must be the vault's |
| *Open the directory*, *Show in folder* | always | `openDirectory`, `revealFile` |
| *Force pull…*, *Force push…* | a folder's vault | the page count from `GET …/removal`, a warning (`keeping.forceAsk`), then `…?force=pull` or `force=push` |
| *Remove the vault…* | always | the counts from `GET …/removal`, the question `keeping.removalAsk`, then `DELETE …?files=keep` or `files=trash` |
| the switches | a folder's vault | `PATCH {pull}`, `PATCH {push}` (`setPush`, above), a work folder's `PATCH {library}` |

The changes and the rounds (*pause*, *resume*, *sync*, *Pull now*, *Push
now* and the switches) leave the panel open on the new state
(`STAYS_OPEN`); the others close it, since they ask first or open
something. *Remove the vault…* offers *Remove, keep the files* and
*Remove, move them to the trash* (the files the sync wrote go to
`.gamma/trash`; files the user added or changed stay). A vault of one file,
a vault whose directory is not there and a stopped one (its marker gone or
another's) offer only *Remove the vault*, which touches nothing on disk.
The pages stay in every case but a work folder's files that are not in the
library, which go to Recently deleted; the question says both numbers
(`removal`'s `files` and `in_library`). A remote's vault then has its token
revoked (`releaseRemote`).

A folder's vault reads, worst first: *Choosing files* for a work folder
whose files are not chosen yet (drawn as pending; its row has *Choose
files…* in place of *pause*, and *sync* shut); *Syncing 30 / 120* while a round
runs, the files push has taken and the pages pull has looked at, of those
the round knows so far (the status's `progress`, written as the round goes),
its tooltip the bytes of the PDFs it pushes and the file at hand, or
*Syncing…* before the round has compared the folder with
the directory or when its round says nothing of itself; *Paused · 14:02*;
*Stopped* (its marker gone or another vault's: *Locate…* says where the
directory is now); *Held · 12 of 20 files gone* (the guard against many
files gone at once: nothing was pulled or pushed until *Go on…*);
*Directory not there* (a disk unplugged, drawn as waiting, never as an
error); *Sync problem*; *Not synced yet*; then *Up to date · 14:02*, with
*n pushed*, *n conflicts*, *n changed here* (files a force pull replaces),
*n not synced* (the other files its last round kept, and the names its
scan refused: two that differ only by case, one Windows refuses, a link),
*n in another vault* (pages filed in it that an older vault beside it
keeps, `status.shared`) and *push turned off* (two folders kept before
that pushed into each other: the newer one's push went off at its upgrade,
`status.push_off`) when there are any. Its tooltip says which way it goes
("Gamma's changes are pulled; nothing here is pushed.") and names the
conflicts, the files kept or refused with why, why push went off, the
pages the other vault keeps, when a folder kept before became a vault
(`upgraded_at`) and the untracked files. A vault of one file reads *Pushing…*, *Paused · 14:02*,
*Problem* (the reason as the tooltip), *Waiting for the file to finish* (a
file still being written or not there, drawn as pending, never as an
error), *Not pushed yet*, *Pushed · 14:02*, or *Up to date · 14:02* when its
rounds found the file Gamma's already; when the last push could not move
the highlights, the tooltip says why. `link.push` is a word (`off`,
`pdfs`, `everything`), read with `keeping.pushWord`, never as a flag:
`Boolean("off")` is true.

A detached clone or link or a paused vault never raises the button's
summary; when everything is, it reads *Paused*. A held vault ranks with
the conflicts, as asking for a look. A Zotero link's PDFs that wait on
something outside Gamma (a full storage, files Zotero does not serve) read
*Waiting*, and its changes queued for a later round *Changes queued*: they
only say so, ranked with a first sync not yet run, below anything that
asks for a look. Conflicts and a clone's or link's cadence stay in Gamma's
own sync pill, in the workspace; a vault's conflicts are on their blocks'
merge chips.

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
action, value), so they need no open server.

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
lights inset on macOS). On Windows the bar keeps its right 146 px free for
those controls, which fits Windows' three 46 px buttons. On Linux the
overlay follows the desktop's own title-bar layout, GTK's decoration layout,
mirrored in a right-to-left UI: `lib/windowControls.js` reads it (GNOME's
`button-layout` setting, else GTK 3's `settings.ini`) when the window opens
and on each focus, `barState().wco` names the ends that hold a button
(`left`, `right`, `both` or `none`), and the bar keeps 146 px free at each
of them and moves its menu past a left-hand one. The `titlebar-area` env()
values reach only a window's own page, never a view like the bar. The logo opens the launcher, which lists every
server (also `Ctrl/Cmd+Shift+L`). Then the switcher: click the name → dropdown of
the open server's Gamma workspaces (check on the current one, role or
*personal* per row, the clone chips), then every server (running /
reachable dot, a running local one's address, check on the current one),
then *All servers…* (the launcher again). At the right: the sync button and its panel (above, "The sync
panel"), the update pill (only while an update is ready, see
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
line + check / download / restart button), and, in a dev run only, the
server overrides (*Advanced: run local servers from a source checkout*); a
packaged build runs its bundled server and nothing else.
When a server will not open, the launcher shows a **failure panel** instead
of a raw message: one sentence naming the cause, with the underlying line
(the server's log line, or Chromium's `ERR_…`) underneath. Below it sits the
button that would fix it: *Open data folder* when the files are the problem,
*Use http:// instead* for a refused `https://` address, *Try again*, and
*Show log file* for a local server. The run's output is folded away under
*Server output*. `lib/startup.js` does the matching: a sidecar that died
during startup from the lines it printed, a URL that would not load from
Chromium's error. Anything else falls back to the plain message. A data
directory the server cannot upgrade is no startup failure: the server stays
up and the window shows its guidance page
([docs/dev/desktop.md](../../docs/dev/desktop.md)). The storage
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

**The iPad and Android apps show these pages too.** Each copies `ui/`
into the app and shows `bar.html` above its web view and `launcher.html`
over it, with a `gammaShell` of its own, in Swift and in Kotlin
([docs/dev/ipad.md "The chrome"](../../docs/dev/ipad.md#the-chrome),
[docs/dev/android.md](../../docs/dev/android.md)). A change to the bar or
the launcher is a change to all three shells. Their `gammaShell.platform`
is `ipad` or `android`, which each page puts on `body[data-platform]`, and
each page's `SHELLS` table, which names those two, sets `body[data-touch]`
for them and holds each one's words: the device ("this iPad", "this
device"), the product in the launcher's footer ("Gamma for iPad", "Gamma
for Android") and the shortcut hints (⌘⇧L, ⌘R and ⌘⇧B; Ctrl+Shift+L,
Ctrl+R and Ctrl+Shift+B). A platform the table does not name is the
desktop's. What only the desktop does is hidden in the apps in two ways,
neither of which changes the desktop. Their state has no `keeping`,
`update` or `settings`, so the bar shows no sync button, *clone* chip or
update pill, and the launcher no Settings block. And one rule in
`theme.css`, `body[data-touch] .desktopOnly`, hides *New local server*
and a local card's Remove. Under the same attribute `theme.css` and
`bar.html` take touch sizes: a 44 px bar, 36 px targets, no 146 px
reserve. Only the iPad's bar adds the status bar's inset above its row
(`--inset-top`); the Android app places its views below the status bar.
The bar's status stays on one line. One control is the apps' own: a
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

*Server → Open a PDF…* asks for one with the native picker
(`openPdfDialog`) and hands it over the same way.

Each file is **read where it is**, one at a time (`openPdfFiles` →
`readPdf` in `main.js`): a page of the app's own local server reads it in
place, so nothing is copied, and the file is never sent to a remote server
(at most its hash, step 2).
A paper opened on a train does not go to the NAS.

1. The window comes forward. With no server open, the host opens (the
   first local server, made when there is none).
2. The place (`openFiles.readerPlace`, from `GET /api/session`): on an open
   local server, the workspace the window shows (`?ws=`, else the
   account's default) when the account writes there and it is no clone.
   Otherwise, a remote server open or no such workspace, a page of the
   workspace the window shows that carries the file's bytes opens there
   first, as an added file's page does (`openCarried`: the file hashed here,
   `openFiles.docIdOf`, and only the hash asked, `GET
   /api/blocks/by-doc/<hash>`, so a paper annotated on the NAS, in a clone
   or in a workspace the account only reads opens its page); a remote's
   session is not read for it, so a NAS out of reach still lets the file
   be read here. No such page, the window moves to the host and the file
   goes into the account's personal workspace there; signed out, a dialog
   says to sign in. A workspace others see
   (other members, or public) asks first, since every member could read the
   file through the page while Gamma runs: *Open in <personal workspace>*,
   the default, *Open in <workspace>* and *Cancel*.
3. `POST /api/folder-links/open {path}` with the shell's word ("Synced
   vaults"), on the server's own origin, answers `{workspace, page,
   created, title}`. The server opens a file inside a synced vault as that
   vault's page, bytes a page of the workspace stores as that page, and a
   file opened before as its page again; anything else
   is a new page inside the one page *Opened files* (a page inside a page
   stays out of the library's list), titled with the file's stem, its PDF
   read from the path (the page's `doc` at `path`).
4. For a new page the annotations another reader saved in the file become
   Gamma's (`POST /api/import/pdf-annotations` with the hash of the page's
   `doc`, read from `GET /api/blocks/<id>`, as below).
5. The bar says where the file is read ("main is read from this computer,
   under Opened files in Personal on Local."), every time, a page that was
   there already too, and the window loads `<origin>/?ws=<id>&page=<id>`.

The page shows *On this computer* in its header, and *Add to library…*
stores the bytes and files the page where the user asks, in this workspace
or another this account edits on the server, with *Keep pushing from the
file* to make it a vault of one file (`POST
/api/folder-links/opened/<id>/add`, the page's own). Its *Another server…*
posts `carry-to-server`, and `carryToServer` adds the page to the library
of another server of the registry (`carryPage`):

1. It checks the page: it reads a file here that is there
   (`GET /api/folder-links/page/<id>`'s `reader`), the window's account
   opened that file (`reader.mine`), and it has a PDF of its own (its
   `doc`: format `pdf`, a hash, no `copy`). As with another
   workspace of this server, carrying an opened PDF away is its opener's
   alone: the file is on that account's computer, and another editor of the
   workspace would take that account's page away with its notes and chat.
2. It asks which server, before any server is started or reached: one
   question, even with one other server, and an info box when the registry
   holds no other. Then, when the account writes in several workspaces
   there, which workspace (`openFiles.carryPlaces`: the personal one first,
   no clone). A remote is read through the window's session, a local server
   through its own, started when it is not running. The window must still
   show the page's server (`stillOn`).
3. It refuses when the target has the page's id, or a page of its PDF
   (`GET /api/blocks/by-doc/<hash>`), live or in Recently deleted: a 404
   that names `trashed` holds the page too, and the merge would replace
   such a copy for good (`holds`). It refuses as well a target whose
   change feed (`GET /api/sync/changes?limit=1`) names an older workspace
   data format than the page's server: the pages go in that server's
   shape, which an older release cannot read. The export is stamped with
   its release besides (`PRAGMA user_version`), and such a release refuses
   it on its own ("made by a newer Gamma").
4. `GET /api/folder-links/opened/<id>/carried` gives the digest of what
   the export takes (the page and the pages inside it, their blocks,
   comments and chats) and those pages' ids; it refuses while a page inside
   reads a file that is gone or changed. Each page inside is looked for on
   the target as in step 3. Then `GET /api/pages/<id>/export?mode=gamma&sub_pages=1`
   exports the page
   with the pages inside it, the PDF's bytes taken from the file and the
   link to the file left behind (the document at `store` in the export).
   The zip must hold the document's stored name, `uploads/<hash><ext>`
   (`lib/zipNames.js`): a file gone or just changed is left out of an
   export, and the carry stops. So does a page inside it whose PDF the
   target has a page of, which the merge would skip. Nothing has been sent
   to the target yet.
5. `POST /api/import-data?mode=merge` on the target, which must add the
   page, and add every page inside it (`pages_skipped` none). Then `PUT
   /api/blocks/<id> {properties: {parent_page: null}}` takes it out of the
   *Opened files* it does not have there.
6. Only then does the page here go: `POST
   /api/folder-links/opened/<id>/carried {digest}` moves it to Recently
   deleted here with the pages inside it, when the digest is still theirs,
   as a carry to another workspace of the server moves it
   (docs/dev/synced_vaults.md "To another server"). Their links to the file
   go with it, so the file opened again is a new page, not this one back,
   and the page restored comes back whole. Nothing is deleted for good. The
   file stays as it is. The call goes to the page's server at its address
   now, through the window's session, and again through that server's own
   session on a 401 (the window signed in to another server on this
   computer meanwhile, whose cookie a port does not tell apart); if it still
   fails, the page is in both places and the message says to delete the one
   here to keep only the copy there. The window opens the page on the
   target.

A page written on here since the digest (a note, a page inside it, a
comment or a chat) gets 409: nothing here was deleted, the copy there
stays, and the shell says the page is in both places now. A failure before
that step leaves the page here as it was and says why. The shell's word
goes on none of these calls.

**Added instead.** When the account set *Add opened PDFs to the library*
(`openAddsToLibrary` in `GET /api/prefs/profile` on the open server), and
always in the Mac App Store build, whose servers reach only the folders the
app held when they started, a file is added as an upload in Gamma adds it
(`addPdf`), through the content session:

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
   (`embAnnots` in `GET /api/prefs/profile`; with `openAddsToLibrary`, the
   only preference names the shell reads). A failure here leaves the page
   as it is.
6. The window loads `<origin>/?ws=<id>&page=<id>`.

A PDF dropped on the window is Gamma's own upload, into the place the drop
names. Under the test harness the question takes its first answer, and
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
menu, the tray menu, or `offerBackground`, the offer made when a clone or
a synced vault is first created. On, `window-all-closed` leaves the app
running with a tray icon (`ensureTray`: *Open Gamma*, the two switches,
*Quit Gamma*). At launch `startBackgroundHosts` then starts every local
server instead of only the hosts of clones, of remote servers' synced
vaults and of Zotero links, so clones, Zotero links and synced vaults
keep syncing with no window. The tray's click or *Open Gamma* brings the
window back where the user left off (`showWindow`). Turning the setting off
from the tray with no window quits.

**Bringing the window back.** Every way back goes through `showWindow`: the
tray, *Window → Show Gamma*, the Dock icon (`activate`) and a second start.
A minimized or hidden window is restored and shown; a closed one (none, or
one already destroyed) is made again, on the last server when *Reopen last
server at launch* is on, else on the launcher; either way it ends focused.
*Show Gamma* is in the Window menu with or without a window, since macOS
keeps the app and its menu bar after the window closes (App Review asked
for it: the Mac App Store build 1.0 (2) was turned down without one). The
window made again reuses the local server still running: `sidecar.start`
hands back the process it holds while that answers `/api/health`, or still
runs and answers within the start budget (`stillUp`: one busy with a large
sync, or on a machine that just woke). Only a process that has exited, or
never answers, is replaced, and it is stopped first, so two servers never
run over one data directory. A `closed` event clears the shell's state
only for its own window, never for one made since.

**Start at login** (`settings.openAtLogin`) registers the OS login item
with `--hidden`, so a login start with the background on makes no window
and goes straight to the tray (`applyLoginItem`; macOS passes a login item
no arguments and reports the login start itself through
`wasOpenedAtLogin`). On Windows and macOS that is `app.setLoginItemSettings`;
on Linux an autostart entry under `~/.config/autostart`. Packaged builds
only, and nothing under the test harness. The Mac App Store build has no
login item (a sandboxed app needs a login helper of its own, which it does
not have): the launcher and the menus leave the switch out.

### Under the test scripts

`test/e2e.js`, `test/security.e2e.js` and, outside CI, `test/smoke.js`
launch the shell with `GAMMA_SHELL_MINIMIZED` (`test/quiet.js`; a run under
`--headed` leaves it out, `tools/headed.cjs`). The shell takes it only with
`GAMMA_SHELL_TEST` or `--smoke` (`TEST_MINIMIZED`). Under it the window
opens minimized and stays down, so a run leaves the screen and the keyboard
alone ([the survey](../../docs/research/test-windows.md)):

- On Windows `setFocusable(false)` comes before `minimize()`: minimizing a
  window never shown takes the foreground otherwise. The call also drops
  the taskbar button.
- A PDF handed over and a sign-in come back go through `surfaceWindow`,
  which leaves the window down. `showWindow` (*Show Gamma*, the Dock icon)
  brings it back with `showInactive`, unfocused, and the background step
  that tests this puts it down again. The restore and focus a user gets run
  only under `--headed`. On Windows a step that leaves the window up fails.
- A question's box (`ask`) takes its test answer and is only recorded, in
  the hook's `asked`.
- An error's message box is logged instead (`errorBox`), and an uncaught
  error exits 1 rather than waiting in Electron's own box. The scripts print
  those `[shell]` lines, which the Playwright driver would otherwise keep.
  A step of `test/e2e.js` that opens a box fails, on any system: it could
  otherwise go on to pass.
- A minimized window on Windows has no client area, so its pages would lay
  out at 0 × 0. The shell gives each page its view's size by device
  emulation (`emulateViewSize`) once the view has navigated, since the call
  crashes Electron before a view has a renderer, and again with every
  navigation. `layout()` keeps a minimized window's views at its normal
  size in any case.

CI's smoke run keeps the normal window: the release's check is of the path
users get.

## In-app updates

`lib/updater.js` wraps `electron-updater`, VS Code style: a silent check
15 s after launch and every 4 h, download in the background, then one
attention-seeking control — the **Restart to update** pill in the shell bar
(and the same action in the launcher's Updates row). *Help → Check for
Updates…* is the only flow that answers with a dialog; under the test
harness that dialog is only recorded (`ask`, the hook's `asked`) and takes
its last button, *Later* or *OK*, which installs nothing. The feed is the
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
  link that is not detached, "The sync panel"), `lastOpened`,
  `windowBounds`,
  and `settings` (`openLastOnLaunch`, `lastTheme`, `dataRoot`, `background`
  and `openAtLogin` (the tray, above), `folderHost` (the local server that
  keeps the synced vaults of remote servers), the dev-mode
  `pythonPath`/`backendDir`/`staticDir` overrides, which a packaged build
  ignores). Local admin credentials are stored in plaintext here — same
  trust level as the SQLite files next to it; acceptable for a per-OS-user
  desktop app. The Mac App Store build
  also keeps `access` here: the vaults' directories and the files pages
  are pushed from, outside its sandbox, that the user picked, as
  security-scoped bookmarks
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
  (accelerators only; hidden behind Alt on Windows; *Window* holds *Show
  Gamma*, *Help* holds *Check for Updates…*), IPC for the shell pages, auto-login script, navigation guard
  (only registered server origins may load in the content view; everything
  else — `target=_blank`, cross-origin redirects — opens in the system
  browser; a server's Gamma Cloud sign-in start is taken over, "Gamma Cloud
  sign-in"), the remote health probes, the `/api/session` read behind the
  workspace switcher, the clone and synced-vault flows over the server's
  API with the vault dialog and the shell's word ("Synced vaults"), the
  focus hint, the tray and background mode, one app per profile and the
  PDFs the system hands it, read where they are or added, and an opened
  PDF carried to another server ("Opening PDF files"), `--smoke` self-test,
  sidecar cleanup on quit, the `GAMMA_SHELL_TEST` hook the e2e suite drives.
- `preload.js` — exposes the `gammaShell` IPC bridge **only in the main
  frame of the bundled launcher and bar URLs** supplied by the main process;
  other local files receive no bridge. On server pages it exposes nothing,
  reports `data-theme` changes and passes on the page's asks about this
  computer (`PAGE_ASKS`, "Synced vaults").
- `ui/tokens.css` — a copy of Gamma's design tokens (see Theme);
  `ui/fonts/` — Inter, Latin subset, with its licence.
- `ui/theme.css` — the Inter `@font-face`, Gamma's keyboard focus ring and
  its unified control classes with the app's recipes (`uiBtn` and its
  primary / ghost / danger / `sm` / `iconSq` variants, `ctlBtn`, `uiInput`,
  `switch`, the `dot` states) for the shell pages. The pages take every
  size, radius and shadow from the tokens (`--fs-*`, `--radius-*`,
  `--shadow-*`, `--font-mono`), icons at Gamma's 14 / 16 / 20 px with its
  1.6 px stroke, and section labels in Gamma's sentence case; and the
  iPad and Android apps' rules (`.desktopOnly`, touch sizes under
  `body[data-touch]`; "Window").
- `ui/bar.html` — the shell bar. `ui/launcher.html` — the server picker.
  Both plain HTML, no build step, and the iPad and Android apps' chrome as
  well.
- `lib/registry.js` — `servers.json` load/save, add/rename/remove,
  last-opened, settings, window bounds, data-dir size, the local servers
  started at launch (`launchHosts`); no Electron, unit tested by
  `test/registry.test.js`.
- `lib/sidecar.js` — local server lifecycle: the port (`pickPort`, "A local
  server's address"; `test/sidecar.test.js`), spawn, health poll
  (`/api/health`, 60 s budget for frozen cold starts), the running server
  handed back to a second start (`stillUp`), log capture, `stopAll` at
  quit (the servers still starting too, so none is left running),
  tree-kill on Windows. Which backend runs (`resolveBackend`, tested there
  too): in a packaged app only the bundled frozen server; in a dev run the
  settings' pythonPath/backendDir/staticDir, else the repo's `backend/venv`
  and `frontend/dist`.
- `lib/startup.js` — why a sidecar would not start: the per-run tail of its
  log (the file is appended to across runs) and a rule table turning the
  server's last words into one sentence, the matched line, and the `action`
  the launcher offers as a button; also the wording for a URL that would not
  load. No Electron, unit tested by `test/startup.test.js`.
- `lib/updater.js` — the electron-updater wrapper described above.
- `lib/shellSecurity.js` — which page in which view the `gammaShell` bridge
  and the privileged IPC handlers trust: the bundled bar and launcher in
  their own top-level frames (`trustedShellSender`, `isShellPage`); no
  Electron, unit tested by `test/main.test.js`.
- `lib/keeping.js` — what this computer keeps, read as items and one
  summary for the sync button ("The sync panel"), and what the shell says
  about a vault: the dialog's sentences, the questions before a force, a
  removal or going on, a round's outcome; no Electron, unit tested by
  `test/keeping.test.js` (`npm test`).
- `lib/cloudSignIn.js` — the loopback return of a Gamma Cloud sign-in: the
  listener, the verifier and its challenge, the tab's last page, the claim
  request ("Gamma Cloud sign-in"); no Electron, unit tested by
  `test/cloudSignIn.test.js`.
- `lib/openFiles.js` — the PDFs a command line names, the workspace a file
  goes into, added or read where it is (and a directory's vault with it),
  and the workspaces of another server an opened PDF may be carried to
  ("Opening PDF files"); no Electron, unit tested by
  `test/openFiles.test.js`.
- `lib/zipNames.js` — the entry names of a zip, read from its central
  directory: whether a page's Gamma export holds its PDF before it is
  carried to another server ("Opening PDF files"); no Electron, unit tested
  by `test/zipNames.test.js`.
- `lib/loadPage.js` — `webContents.loadURL` without its false alarms. Its
  promise takes any `did-stop-loading` or `did-navigate-in-page` for the end of
  its load, and the load before it or the page being left can send those, since
  Gamma writes its workspace or page into the address as it boots: a load
  starting just then is rejected as `ERR_FAILED`, or resolved before it has
  begun, while it goes on and lands, which is what a server opened and then its
  workspace ran into. A load cancelled outright is rejected the same way with
  no `did-fail-load`, so only an ending with one of those two about counts, and
  never one after the redirect guard refused the load: then `loadPage` waits
  for the page, up to a minute, and a newer load aborts the wait as `loadURL`'s
  own watch would have.
- `lib/hostPath.js` — a path as programs outside a Store package find it:
  the package's private copy of AppData ("Shell state"); no Electron, unit
  tested by `test/hostPath.test.js`.
- `lib/folderAccess.js` — the Mac App Store build's folders, vault
  directories and pushed files outside its sandbox: the bookmarks kept, the ones opened at
  launch, and when a
  server must start again to reach one ([release.md](release.md#mac-app-store));
  no Electron, unit tested by `test/folderAccess.test.js`.
- `lib/microphone.js` — the pages' permission requests: all granted, the
  microphone on macOS once the system allows it ("The microphone"); no
  Electron, unit tested by `test/microphone.test.js`.
- `lib/windowControls.js` — on Linux, the ends of the shell bar the window
  controls take, from the desktop's title-bar layout and the UI's text
  direction (the shell bar, above); no Electron, unit tested by
  `test/windowControls.test.js`.
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
  DLL search path. PyInstaller and pyinstaller-hooks-contrib are pinned in
  its `PYINSTALLER`, which it installs into the running Python when the
  installed versions differ, on the release runners and by hand alike.
- `assets/icon.png` — app icon (1024 px, the favicon mark, from the brand
  generator; electron-builder derives ico/icns and the Linux icon set, and
  App Store Connect wants the icns's 1024 px image). `assets/entitlements.mac.plist` —
  hardened-runtime entitlements for signed mac builds, the microphone
  among them ("The microphone").
- `assets/appx/` and `assets/store/` — tracked Store package/listing images;
  `npm run store-art` regenerates them (`tools/branding/build.mjs`). The
  packager's build-resources directory is `assets/`; `build/` holds
  generated PyInstaller intermediates.
- `test/main.test.js` — `main.js` under plain Node, Electron replaced by a
  stand-in and the local servers by fakes (the `GAMMA_SHELL_TEST` hook):
  the launch record a keeping reading writes, a resume made during a
  reading, one start per server, the requests each vault action sends, a
  round followed to its end with no request held open, the shell's word on
  the routes that take a path and on no other, a directory's vault made
  with no server open, the dialog's answers, the focus hint's pace, the
  hint about a third file, the carry to another server and its refusals
  (`npm test`).
- `test/e2e.js` — the Playwright-driven end-to-end suite (see
  [checklist.md](checklist.md)). `test/security.e2e.js` — the Electron
  boundary against a fake remote server, no backend ("Invariants").
  `test/smoke.js` — runs the app's `--smoke` self-test (dev or
  `--packaged`). All three keep the window minimized through
  `test/quiet.js` ("Under the test scripts").
- `test/headed.test.js` — `tools/headed.cjs`, the one switch for a test
  run's windows, and that every suite in the repo reads it rather than
  deciding for itself (`npm test`).

## Invariants

- The shell must keep treating Gamma as a black box: talk to it only via the
  public HTTP API + env config (`GAMMA_DATA_DIR`, `GAMMA_STATIC_DIR`,
  `GAMMA_ADMIN_USER`, `GAMMA_ADMIN_PASSWORD`, `GAMMA_VERSION` = the shell's
  own version, so the server's admin dashboard names the app,
  `GAMMA_UPDATES` = `desktop`, or `store` in a Microsoft Store install, or
  `mas` in the Mac App Store build (which also sets `GAMMA_UPDATE_CHECK=off`),
  so it says who installs a newer one,
  `GAMMA_CLOUD_DEFAULT_ISSUER` = Gamma Cloud, the account server until the
  admin saves another, `GAMMA_FOLDER_LINKS` = this server keeps synced
  vaults and reads PDFs where they are, anywhere the user picks, since the
  sidecar runs as the user, `GAMMA_SHELL_SECRET` = the shell's word for the
  routes that take a path, "Synced vaults"), `/api/health` and
  `/api/session` (+ the `?ws=` and `?page=` URL parameters). No imports from `backend/`,
  no frontend patches. The one thing it reads off the page is the
  `data-theme` attribute (read-only, via the preload); the page's asks
  about this computer are passed on as messages the shell checks, and
  none of them carries a path.
- Server pages never get an IPC bridge; `gammaShell` exists only on the
  bundled launcher and bar in their top-level views. Every privileged
  handler checks the exact page URL, sender web contents and main frame
  (`lib/shellSecurity.js`); a `file:` URL alone grants nothing. Remote
  workspace metadata, including roles, is rendered as text in the bar.
- `ui/` is also the iPad and Android apps' chrome ("Window"). A page keeps
  working under an app's `gammaShell`, which has only the calls the app
  answers and no `keeping`, `update` or `settings` in its state: a control
  only the desktop answers either reads one of those keys or carries
  `.desktopOnly`. The one only the apps answer, the bar's hide chevron,
  reads `barHideable`, which only their state has.
- Local sidecars bind `127.0.0.1` only (the LAN-exposed use case is the
  existing server/Docker deployment, not the desktop app).
- Navigation allowlist: only registered server origins render in the
  content view; foreign URLs (including `window.open` and cross-origin
  redirects) go to the system browser. A Gamma Cloud sign-in runs there
  too, and its session comes back only through a loopback address and a
  verifier the shell holds ("Gamma Cloud sign-in").
  Arbitrary local files and non-web schemes are blocked, and the bar may
  navigate only to its own bundled page. `node test/security.e2e.js` verifies
  this boundary in real Electron against a synthetic remote server and a
  throwaway profile; it starts no Gamma backend.
- The updater never installs without the user's click (the pill / button /
  dialog); automatic work is limited to checking and downloading.
