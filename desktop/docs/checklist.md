# Desktop QA checklist

What to verify before cutting a release, and how much of it the suite does
for you. Run the automated part on the packaged build (that is what ships):

```bash
python desktop/build_backend.py && cd desktop && npm run pack && npm run e2e:packaged
```

`npm run e2e` runs the same suite on the dev tree (sidecars from
`backend/venv`, frontend from `frontend/dist`) in ~3 min; `--continue` keeps
going after a failure, `--keep` leaves the temp profile behind for a look.
The suite uses a throwaway userData profile, so your real servers are
never touched. The window stays minimized, so a run does not take the
screen; on Windows it also takes no focus and has no taskbar button, and a
step that leaves it up fails. The background step brings it back for a
moment, unfocused, to test that. The restore and focus a user gets run under
`--headed`, which keeps the window up to watch. A step that opens an error box
fails too, on any system. Each item below names the e2e step that covers it
(**auto**) or says it is a manual look (**manual**).

## 1. Lifecycle

| # | Check | How |
|---|---|---|
| 1.1 | First start shows the launcher; the shell bar reads *Servers*; layout is bar 38 px + content below; the *Advanced: run local servers from a source checkout* settings show in a dev run only | auto: *first start shows the launcher + shell bar* |
| 1.2 | Create a local server → card appears, data dir created under `workspaces/<id>` | auto: *create a local server from the launcher* |
| 1.3 | Open it → sidecar starts on a free `127.0.0.1` port from the dynamic range (49152–65535), Gamma loads, silent admin login lands, bar shows the name + reload button | auto: *open it: sidecar starts, Gamma loads, auto-login lands* |
| 1.4 | Quit → every sidecar process is gone (no orphan `gamma-server.exe`/python), window bounds saved | auto: *quit: every sidecar stops, window bounds persist* |
| 1.5 | Relaunch → the last server reopens by itself, at the same address, data intact, chrome already in the persisted theme | auto: *relaunch reopens the last server with its data intact* |
| 1.6 | *Reopen last server at launch* switched off → launcher at start | manual (toggle in the launcher, restart) |
| 1.7 | Cold start of the frozen server stays well under the 60 s health budget on a slow disk | manual: watch the *Starting …* status; `logs/<id>.log` has the uvicorn banner |
| 1.8 | A server that will not start explains itself on the launcher (cause, the server's own line, the fixing button, log folded away); *Open data folder* and *Show log file* open the data folder and show the log in the file manager | auto: *a server that will not start is explained, not dumped* (the two buttons as recorded under the harness); manual: open a server whose data folder Gamma cannot write (make it read-only). A data folder written by a newer Gamma is no startup failure: it opens on the server's guidance page in the window |
| 1.9 | A local server keeps its address: the bar menu shows `127.0.0.1:<port>` beside a running local server, its launcher card shows the address with *Copy address*, the credentials dialog names it, and a restart or relaunch comes back on the same port; a port another program took is given up for a new one, shown in the same places | auto: *the bar menu shows each running local server's address …*, *launcher lists sizes …*, *storage folder: …* and *relaunch …* assert the same origin; `npm test` (`test/sidecar.test.js`): the port another program holds, a new one from the dynamic range; manual: the Gamma Connector set to that address, signed in with the credentials, saves a paper into the server, and still does after a relaunch |

## 2. Switching (the reason the shell bar exists)

| # | Check | How |
|---|---|---|
| 2.0 | Bar dropdown lists the open server's Gamma workspaces (check on the current one, role per row); choosing one navigates to `?ws=<id>` on the same server and the bar names it. Opening a server the window is signed out of and then at once one of its workspaces, as an origin's or clone's chip and a PDF opened with Gamma do, signs in and shows that workspace when the open resolves, with no error box | auto: *Gamma workspaces: the bar lists them and switches with ?ws=* |
| 2.1 | Bar dropdown lists every server with running dots, check on the current one, *All servers…* | auto: *second server; switch from the shell bar while Alpha keeps running* |
| 2.2 | Switching to another local server starts its sidecar; the first one keeps running | auto (same step) |
| 2.3 | Switching back is instant and hits the same server process (no restart, session still valid) | auto: *switch back to Alpha: instant, same server, no restart* |
| 2.4 | The dropdown closes on choose / a press outside it / Esc, and the bar shrinks back to 38 px | auto (bar height asserted) + manual feel |
| 2.5 | The logo, `Ctrl/Cmd+Shift+L` and the *All servers…* item go to the launcher | auto: *launcher lists sizes …* clicks the logo; accelerator and menu item manual |
| 2.6 | A server added while another is open shows up in the dropdown | auto (Beta is added mid-session) |
| 2.7 | Clones: on a remote server a workspace row shows *clone* on hover; choosing it makes the clone on the local server and moves the window there; the clone's row reads *clone* with an *origin* chip back to the remote; the remote row now shows *open clone* and opens the clone; a second *clone* opens the existing clone instead of making another | auto: *clone: the "clone" chip on a remote row makes the clone; the rows then cross-link* |
| 2.8 | A library folder's right-click *Sync with this computer…* and the sync panel's *Sync a folder of …* both ask for a directory, then show the vault dialog in the panel: *Local*, *Remote*, what the directory holds, *Pull* on and *Push* off for a folder with pages, one sentence on the first round, *Sync* shut while that sentence says why not. *Sync* makes the vault: by the server itself on a local server, by the host local server with a read token minted on the remote on a remote one, no clone, and the folder's notes and PDFs land in the directory. Asked again, it opens the directory. The panel lists the vault under *Synced vaults* with its direction marks and its state, `path ⇄ remote · folder` in its tooltip, its buttons shown without hovering; *pause* leaves the panel open on the paused row and *resume* starts it again; *Sync now* runs a round | auto: *synced vault of a folder: …*, *synced vault from the library: …*, *synced vault from a remote: …*; manual: the native picker, a click in the page while the dialog waits (it stays), Escape (it cancels) |
| 2.9 | *Keep running in the background* on: closing the window leaves the tray icon and every local server running; the tray's *Open Gamma*, *Window → Show Gamma* and the Dock icon each bring the window back on the same sidecar process; a minimized window comes back from either; off, closing the last window quits (macOS keeps the app in the Dock, 2.14) | auto: *background: with "keep running" on, closing the window leaves the servers up and the tray in place; Window → Show Gamma and the Dock icon bring it back over the same server* (the menu item clicked and `activate` emitted on Windows); manual: *Start at login* on a packaged build launches hidden into the tray |
| 2.10 | At launch the local servers that hold clones or a Zotero link that is not detached, and the host of remote servers' synced vaults, start in the background (their dots turn on) even when a remote server is the one open; a server whose links were all detached or removed does not, once the shell has read its links while it ran; with *Keep running in the background* on, every local server does | `npm test` (`test/registry.test.js`: which servers start; `test/main.test.js`: what a reading records, one start per server); manual: the dots at launch |
| 2.11 | The sync button sums up every clone, Zotero link and synced vault of the running local servers (*Synced*, *Syncing…*, *Held*, *n problems*, *Waiting*); its panel lists each under its group with its state, opens it on click, pauses and resumes it (a clone or a link detaches and reattaches), and syncs it | auto: *clone: …* checks the panel's clone row, its pause and resume, and the button's state; the vault steps check their rows; every state: `npm test` (`test/keeping.test.js`); manual: a problem, a held vault and a conflict shown in colour, a Zotero link's row, its pause, resume and sync |
| 2.12 | A page's Document menu → *Push from a file on this computer…* asks for a PDF and makes a synced vault of one file: the bar says so, the panel lists it under *Synced vaults*; a new build of the file becomes the page's PDF (at the next tick, or now with the row's *Push now*) and the row reads *Pushed*; asked again, the menu item offers *Show in folder* and *Remove the vault…*, which leaves the file. With two files of one directory pushed already, a third asks *Sync the folder…* / *Push this file only* / *Cancel*. On a remote server the same, with a write token minted on the remote | auto: *synced vault of one file: …*; the third file's question: `npm test` (`test/main.test.js`); manual: the native file picker, a real `latexmk` build (the row reads *Waiting for the file to finish* while it writes), a remote server's page |
| 2.13 | The *Push* segment in a folder vault's view (*…* on its row) turns pushing off, PDFs or everything, and the bar says so in words; with *PDFs* on, a PDF changed in the directory becomes its page's new file at the next round and the row's push arrow lights up. On a remote folder turning it on swaps the read token for a write token | auto: *pushing from a folder's vault: …* (the segment); manual: a PDF annotated in another reader pushed, a remote folder's token swap |
| 2.14 | macOS, background off: closing the window leaves Gamma in the Dock with its menu bar; *Window → Show Gamma* and a click on the Dock icon each bring the window back on the last server, no second `gamma-server` process started (Activity Monitor); a minimized window comes back from either (App Review 4, the Mac App Store build) | manual (needs a Mac) |
| 2.15 | *Add a synced vault…* in the launcher (no server open) and at the sync panel's end: a directory of notes becomes a new folder named after it in the host's own workspace (or the open local workspace only the account sees), pushed and pulled, `.gamma/vault.json` written and nothing else of Gamma's | auto: *"Add a synced vault…" in the launcher: …* |
| 2.16 | A vault's view (… on its row): its everyday actions as a bar of buttons named by their tooltips, where *Pull now* and *Push now* run one way; *Force pull…* and *Force push…* ask with a count first; a vault held by its guard (many files gone at once) reads *Held* and *Go on…* goes on after a question; *Locate…* on a vault whose directory moved, with the same marker, syncs it there; *Remove the vault…* asks keep or trash (only *Remove the vault* for a vault of one file, an offline or a stopped one) and the pages stay; a remote's folder keeps *Pull* on and *everything* shut | `npm test` (`test/main.test.js`: each action's request; `test/keeping.test.js`: the questions); auto: *synced vault from the library: …* removes with the trash; manual: a held vault, a moved directory, an unplugged disk |

## 3. Storage (per-server `GAMMA_DATA_DIR`)

| # | Check | How |
|---|---|---|
| 3.1 | Layout: `users.db`, `workspaces/<id>/pages.db`, `workspaces/<id>/data.db`, `workspaces/<id>/uploads/` (one dir per Gamma workspace; an older data folder is upgraded at first start, snapshot under `backups/`, and one below the upgrade floor opens on the server's guidance page) | auto: *data dir has the standard GAMMA_DATA_DIR layout* |
| 3.2 | An uploaded PDF lands in `uploads/` under its content-hash name, exactly once | auto: *upload a PDF + create a paper page with a math note* |
| 3.3 | Launcher shows size on disk per local server + the *last opened* badge; the card's *Open data folder* and *Show server log* open the folder and show the log in the file manager | auto: *launcher lists sizes, last-opened badge …* (the two buttons as recorded under the harness); manual: the file manager really opens |
| 3.4 | Remove → *keep files* leaves the folder; *delete everything* removes it (only under the default or the configured storage root) | auto (remove) + manual (check the folder) |
| 3.5 | Two servers never share state (separate DBs, uploads, sessions) | auto: Beta starts empty; Alpha's page only appears after the import |
| 3.6 | Storage limits still apply per server (Settings → Users in Gamma) | manual, Gamma's own feature |
| 3.7 | *Local server storage → Change…* + *Move data*: every local server lands under `<new root>/<id>`, old folders gone, sidecars stopped (they start on the next open), library intact; *Use default* moves back | auto: *storage folder: change the root, existing servers move, data intact* (the picker itself is manual) |
| 3.8 | *Only new servers* leaves existing ones in place; the next new server is created in the new root | manual |

## 4. Export / import inside a server

| # | Check | How |
|---|---|---|
| 4.1 | Page export in every mode returns 200 with content: `readable`, `notes-pdf`, `logseq-graph`, `zotero-rdf`, `gamma` | auto: *page exports in every mode* |
| 4.2 | `notes-pdf` and `export-pdf` (annotated PDF) are real PDFs — the frozen bundle carries pypdf, PyMuPDF's native libraries and the ziamath/ziafont fonts | auto (same step; `%PDF` magic asserted) |
| 4.3 | Markdown import creates a page | auto: *markdown import creates a page* |
| 4.4 | Backup zip (*Export my data*) downloads through the browser download path into a file | auto: *backup zip downloads through the browser download path* |
| 4.5 | Save dialog appears for downloads in the real app (tests bypass it via `GAMMA_SHELL_DOWNLOAD_DIR`) | manual |
| 4.6 | *Import data* restores the backup into another server's workspace, uploads included | auto: *import the Alpha backup into Beta* |
| 4.7 | Drag-and-drop a PDF / `<input type=file>` uploads work in the Electron window | manual |
| 4.8 | Zotero RDF and Logseq imports | manual (need real export files) |
| 4.9 | A PDF opened with Gamma while it runs: the second start quits, the PDF is read where it is, its page under *Opened files* of the workspace the window shows, titled with the file's name, with the annotations the file carried, and the bar says where; the same file again opens the same page and imports nothing twice; a PDF named at launch opens in the last server once it is up; *Server → Open a PDF…* does the same from a picker | auto: *a PDF opened with Gamma: …*, *a PDF named at launch …*; the menu item manual |
| 4.10 | Into a shared workspace the app asks first (*Open in <personal>* / *Open in <workspace>* / *Cancel*); with a remote server open, or a clone or a read-only workspace shown, a page there that holds the same bytes opens there, its notes and highlights with it, and only the file's hash is sent; any other file is read on the host, in the personal workspace, and the bar says so; with *Add opened PDFs to the library* on (and in the Mac App Store build) it is uploaded as before, a shared workspace or a clone asking *Add to …*; signed out, a dialog says to sign in | manual (the choices themselves: `npm test`, `test/openFiles.test.js`; the page held there and the host's fallback: `test/main.test.js`) |
| 4.11 | Windows installer, per user and per machine: *Open with* on a `.pdf`, `.md`, `.tex`, `.bib` and `.typ` lists Gamma, and so does Settings → Default apps for each; no default changes at install, and the reader that was the PDF default stays it until Gamma is chosen; *Always* makes a double-click open the file in Gamma, also while it runs in the tray. Right-click a folder → *Open with Gamma* (Windows 10 in the menu, Windows 11 under *Show more options*) makes a work folder or opens its folder; the empty background of an open folder does the same for that folder; the background of a drive root says it is refused, of the Desktop asks first; three new folders selected make one work folder and say for the other two to wait; the verb also works while Gamma runs in the tray. On a machine with no app for `.typ`, a double-click on a `.typ` either shows *How do you want to open this file?* or opens Gamma (record which: both are accepted), and a `.md` or `.tex` with an app chosen still opens that app. An update keeps every entry and a default the user chose; an uninstall removes the folder verb, the ProgIDs, every *Open with* entry and Default apps, leaves no empty `HKCU\Software\Classes\.typ` (or `.bib`, `.markdown`) key that it made, and the PDF opens in another reader again | manual (installed build) |
| 4.12 | **Store package:** the file types as 4.11, no folder verb. **macOS dmg:** *Open With* on a `.pdf`, `.md`, `.tex`, `.bib` and `.typ` lists Gamma (*Change All* makes it the default; a file opened while the app is closed and while it runs); a `.md` still opens TextEdit (or the app it opened before), a `.tex` with TeXShop or TeXstudio installed still opens it; on a Mac with no app for `.typ`, a double-click opens Gamma read-only (accepted); a folder dropped on the Dock icon and `open -a Gamma ~/paper` make or open its work folder, cold and while running. **Mac App Store:** still PDFs only; a folder dropped (if Finder allows) opens the picker at it. **.deb:** the file manager offers Gamma for a PDF, as before; `dpkg -c` lists the service menu (both paths) and the Nemo action; in Dolphin (Kubuntu) and Nemo (Mint) a folder's and a folder background's context menu has *Open with Gamma*, which makes or opens its work folder; `gamma ~/paper` and `gamma notes.md` from a terminal open the folder and the file; `xdg-mime query default inode/directory` and `text/markdown`, and `xdg-open` of a directory and of a `.md`, are what they were before the install; removing the package removes the two entries | manual |
| 4.13 | One app per profile: starting Gamma while it runs (Start menu, the tray in background mode) brings the window forward; no second app, no second sidecar on the same data directory | auto: the second start in *a PDF opened with Gamma: …* quits; the window manual |
| 4.14 | An opened PDF's *Add to library…* → *Another server…*: the app asks which server before it reaches any, then the workspace; the page lands there with its PDF stored, its notes and highlights, out of *Opened files*, and opens there; here it is in Recently deleted, and the file stays. Refused with nothing sent when the file is gone, the target has the page or its PDF, or there is no other server; written on here meanwhile, it stays in both places | auto: *an opened PDF carried to another server: …*; the refusals: `npm test` (`test/main.test.js`) |
| 4.15 | A folder opened with Gamma: a second start hands over a directory, which becomes a work folder of the workspace the window shows, paused and choosing, the window on its tracking dialog; opened again while choosing, the dialog comes back; no work folder at the app's own directory. A directory named at a cold launch becomes a work folder once the app is up, the window shown | auto: *a folder opened with Gamma: …*, *a directory named at a cold launch becomes a work folder once the app is up* |
| 4.16 | Files opened with Gamma: in a work folder, after Save a tracked `.tex` opens its page in the code view, a subdirectory opens its folder, an untracked `.csv` opens *Choose files…* with it ticked; a Markdown file outside any vault asks, *Just view it* reads it where it is under *Opened files* in the code view and the bar says how to edit it, *Edit in Gamma* makes a work folder of its repository with the file ticked and after Save opens the note, where an edit reaches the file | auto: *files of a work folder opened with Gamma: …*, *a Markdown file opened with Gamma outside any vault asks; …*, *Edit in Gamma makes a work folder of the Markdown file's repository, …* |
| 4.17 | A file opened from a NAS-synced directory opens its page on the NAS, never a local copy; with the NAS gone from the registry, the error says to add it | manual (a NAS server and a synced directory) |
| 4.18 | A `.md` double-clicked (Gamma chosen for it) in a repository's subdirectory asks *Edit in Gamma* or *Just view it*: Edit makes the repository a work folder with the file ticked, and after Save an edit in Gamma reaches the file (open it in another editor); View reads it in the code view. A `.md` loose in Downloads asks, then asks again about Downloads; Cancel there makes nothing | manual (installed build) |

## 5. Remote servers + navigation guard

| # | Check | How |
|---|---|---|
| 5.1 | A remote URL loads without auto-login (normal Gamma login), bar shows its name | auto: *remote server: a URL, loads without auto-login* |
| 5.2 | Unreachable server → back on the launcher with the error text | auto: *unreachable remote falls back to the launcher with the error* |
| 5.3 | Remote cards and bar-menu rows carry a reachability dot: green for a live server, red for a dead URL, dim until probed | auto: *remote reachability dot: on for the live server, off for a dead URL* |
| 5.4 | Foreign URLs (`window.open`, `location` changes, `target=_blank` chips) open in the system browser; the window stays on the server | auto: *navigation guard: foreign URLs open outside, the window stays* |
| 5.5 | ChatGPT-OAuth sign-in: the auth page opens in the system browser. On a local server Gamma connects by itself once the sign-in finishes (the tab says "Signed in to ChatGPT"); on a remote one the pasted callback URL, or the device code entered at auth.openai.com/codex/device, completes it | manual |
| 5.6 | Gamma Cloud sign-in (a server's login page, or Settings → Account & sync → Link): the sign-in page opens in the system browser, whose tab ends on "Signed in to Gamma" with no session of its own; the window comes forward signed in | auto (fake account server): *Gamma Cloud sign-in: the system browser signs in, the window gets the session through the loopback return*; against account.gammapdf.com, with Google too: manual |
| 5.7 | HTTPS remote with a self-signed cert shows Chromium's interstitial (expected: use a real cert) | manual |

## 6. Look and feel

| # | Check | How |
|---|---|---|
| 6.1 | Chrome follows Gamma's theme (dark/light/gamma-light/gamma-dark/sepia/solarized/gray) live and persists it | auto: *theme mirror* |
| 6.2 | Windows: title-bar overlay controls recolor with the theme; the bar stops where they begin; the bar area drags the window | manual |
| 6.3 | macOS: traffic lights sit in the bar (`hiddenInset`), bar padded past them | manual (build on a Mac) |
| 6.4 | Launcher and bar use Gamma's tokens/controls/icons (no emoji, no bespoke colors) | manual: compare with Settings in Gamma |
| 6.5 | Alt reveals the native menu on Windows; accelerators work without it (Ctrl+R reload, Ctrl+Shift+I devtools, Ctrl+0/±) | manual |
| 6.6 | Rename from the launcher updates the bar immediately | auto: *rename + remove from the launcher* + bar name via pushState |
| 6.7 | Linux: title-bar overlay controls follow the desktop's title-bar layout (on GNOME only a close button), recolor with the theme and stay clear of the bar's content at whichever end holds them (buttons moved to the left in GNOME Tweaks clear the logo and the switcher once the window is focused again, the menu opens past them); the desktop entry shows the icon in the launcher/dock; `gamma` on PATH starts the app | manual (build on Linux) |

## 7. Updates

| # | Check | How |
|---|---|---|
| 7.1 | Dev tree / test harness: updater reports `unsupported`, the launcher's Updates row says so, no pill in the bar; *Help → Check for Updates…* says updates are not available | auto: *updater: disabled under test, state exposed through the shell* (the menu item clicked, its box recorded) |
| 7.2 | Packaged app, no newer release: *Help → Check for Updates…* says *latest version*; the launcher row reads *Up to date* | manual |
| 7.3 | Packaged app, offline: the row reads *Update check failed: …*, nothing else complains; *Try again* re-checks | manual |
| 7.4 | Windows: install an older version, publish a newer release → within ~15 s of launch the bar shows *Restart to update* (row: *ready; installs when you restart*); clicking it quits, stops every sidecar, installs silently, and the relaunched app reports the new version | manual, needs a real release |
| 7.5 | macOS (unsigned): same setup shows the *Update: <v>* pill; clicking opens the release page in the browser instead of installing | manual (build on a Mac) |
| 7.6 | Linux (.deb): same setup as 7.4 shows *Restart to update*; clicking it opens a pkexec password prompt, `dpkg -i` runs, and the relaunched app reports the new version | manual, needs a real release |

## 8. Release

| # | Check | How |
|---|---|---|
| 8.1 | Version: patch releases need nothing (newest `v*` tag + 1); for a minor/major raise `"version"` in `desktop/package.json` above the newest tag before merging | manual |
| 8.2 | `desktop` workflow, dispatched by the `release` skill (a merge releases nothing): the web app built once; version pinned → freeze → frozen health check → electron-builder → signature verify (ad-hoc on macOS without a cert) → packaged smoke, all three OSes; Linux also installs the `.deb` and smokes `/opt/Gamma/gamma`; the Android APK and bundle (16 KB alignment, signature); publish → release + tag, docker re-tag, Chrome Web Store dispatch, TestFlight marker; then the Microsoft Store and Google Play submissions when their secrets exist ([github_actions.md](../../docs/dev/github_actions.md#desktopyml)) | CI |
| 8.3 | Release page lists `Gamma-<v>-win-x64.exe` (+ `.blockmap`, `latest.yml`), `Gamma-<v>-mac-arm64.dmg`/`.zip` (+ their `.blockmap`s, `latest-mac.yml`), `Gamma-<v>-linux-amd64.deb` (+ `latest-linux.yml`), `Gamma-<v>-android-arm64.apk` (when the Android key is set), `gamma-connector-<v>.zip`, the AI plugins (`gamma-codex-plugin-<v>.zip`, `gamma-claude-code-plugin-<v>.zip`, `dsh-gamma-<v>.tgz`, `dsh-gamma.tgz`, `install-gamma-codex.ps1`/`.sh`) and their `*SHA256SUMS.txt`; the release is NOT a draft / pre-release (installed apps skip those) and is marked *Latest* (an `extension-v*` release must never be) | manual on the release page |
| 8.4 | Fresh machine: installer runs (unsigned: SmartScreen *Run anyway*; signed: no SmartScreen block, *Publisher* shows the cert subject in the UAC prompt; Debian/Ubuntu: `sudo apt install ./Gamma-<v>-linux-amd64.deb` pulls the deps), first launch creates a local server, no Python/Node needed | manual |
| 8.5 | Signed macOS build: `codesign --verify --deep --strict Gamma.app` and `spctl --assess --type execute Gamma.app` pass; a fresh download opens without any dialog (the release job's *Verify signature* step covers this on the runner) | manual |
| 8.6 | Cert-less macOS build (ad-hoc): a fresh download shows *Apple could not verify…*, and *Privacy & Security → Open Anyway* launches it; no *damaged* dialog | manual (needs a Mac) |
| 8.7 | macOS microphone, on the signed build and the ad-hoc one: the first recording into a note asks with Gamma's purpose string, and *Allow* records with sound; with Gamma turned off in *Privacy & Security → Microphone*, a recording is refused and the dialog's *Open System Settings* opens that pane; turned on again and Gamma reopened, it records ([architecture.md](architecture.md#the-microphone)) | manual (needs a Mac) |
| 8.8 | Android, a signed release: the APK installs on a phone or tablet (Android 10 or later, 64-bit ARM) once the browser may install apps, starts its own server and opens the library; the next release's APK installs over it with the library intact; the `play` job's notice names the version on the internal track | manual (needs a device) |

## 9. Mac App Store build

On a development-signed build (`npm run pack:mas-dev`, [release.md](release.md#mac-app-store)),
the only one that runs sandboxed outside the store. The folder access
(9.3–9.6) leans on one assumption no other build can check: a local server
started after the app opened a folder can use it.

| # | Check | How |
|---|---|---|
| 9.1 | Signatures: `codesign -d --entitlements -` lists the sandbox entitlements on `Gamma.app` and sandbox + inherit on `Contents/Resources/gamma-server/gamma-server`; the `.pkg` of `npm run dist:mas` names the installer certificate (`pkgutil --check-signature`) and its files are world-readable | manual (needs a Mac) |
| 9.2 | First launch reaches the launcher; a new local server starts and opens (its data in the container); the launcher's Updates row and *Help → Check for Updates…* say the App Store delivers updates; Settings → Server shows no GitHub check; no *Start at login*, no source-checkout settings | manual (needs a Mac) |
| 9.3 | *Local server storage → Change…* to a folder outside the container (e.g. on the Desktop), *Only new servers*, a new server there starts and keeps a page; quit, relaunch: it opens without asking for the folder | manual (needs a Mac) |
| 9.4 | Sync a folder of the open local server with a new folder on the Desktop: the server restarts on the same address, the page carries on, the Markdown and PDFs appear; after a relaunch the vault still syncs; the same for a remote server's folder (the host restarts) and for *Add a synced vault…* | manual (needs a Mac) |
| 9.5 | Rename that storage folder in the Finder, relaunch, open its server: the failure card says Gamma can no longer open it; name it back, *Choose folder…*, it opens | manual (needs a Mac) |
| 9.6 | *Move data* of the servers to another outside folder and back; *Remove* with *delete everything* on a server outside the container | manual (needs a Mac) |
| 9.7 | A PDF opened from the Finder with Gamma (*Open With*) is uploaded into the open server, as the build always adds opened PDFs to the library; a download (a backup zip) saves through the dialog; recording into a note asks for the microphone with the purpose string, and records; refused, it behaves as in 8.7 | manual (needs a Mac) |
| 9.8 | Quit: no `gamma-server` process is left (`pgrep gamma-server`) | manual (needs a Mac) |
| 9.9 | A page pushed from a PDF outside the container (2.12): a new version is pushed; after a relaunch the next one is too, also when the build tool replaces the file by renaming a new one over it | manual (needs a Mac) |

## Last run

| Suite | Date | Result |
|---|---|---|
| `npm run e2e` (dev tree, Windows 11, minimized) | 2026-11-05 | 35/35 passed |
| `npm run e2e:packaged` (frozen bundle, Windows 11) | 2026-09-02 | 21/21 passed |
| `npm run e2e` (dev tree, Ubuntu under `xvfb-run -a`, Electron 45) | 2026-11-06 | 35/35 passed with *background*'s minimized-window round trip left out: Xvfb has no window manager, so a window there never reads as minimized and the step fails at it |
| `npm run e2e -- --headed` (dev tree, Ubuntu under `xvfb-run -a`, Electron 38.8.6) | 2026-11-07 | 35/35 passed. The same run minimized failed 32 of 35 at its clicks (Playwright waiting for the launcher's buttons to be visible), from the second step on |
| `npm run e2e -- --headed` (dev tree, Ubuntu under `xvfb-run -a`, Electron 38.8.6) | 2026-11-08 | 35/35 passed in 5 runs of 5, with the shell reloading the page after a silent login |

Manual items last looked at 2026-09-01 on Windows 11: 1.6, 2.4, 2.5, 6.2,
6.4, 6.5 (screenshots of the bar + launcher in dark and light). Not yet
exercised: 5.5, 5.6 against account.gammapdf.com, 5.7, 4.7, 4.8, 7.2–7.6, everything macOS (6.3), everything
Linux beyond the CI smoke (6.7, 7.6), 8.4, 8.5, 8.8 (no Android release yet), section 9. The submitted
Mac App Store build 1.0 (2) passed the equivalent of 9.1, 9.3, 9.4 (a
local server's folder) and 9.8 in October 2026, with its own folder
handoff to the server rather than this one's ([research/mac-app-store.md](../../docs/research/mac-app-store.md)).
