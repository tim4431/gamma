# The desktop app on the Mac App Store

Survey from 2026-10-27. The question was what Apple asks of the desktop
app to be sold in the Mac App Store, and how much of the first submission
to take into the repository. That submission, macOS 1.0 (build 2), was
built from v0.3.8 (`68956bd8`) by the collaborator who owns the App Store
Connect record and was handed over as a zip: the changed sources, a patch,
the icon, and notes on what was proven. The build as it now stands is
[desktop/docs/release.md](../../desktop/docs/release.md#mac-app-store);
this note keeps the reasoning and what was left out. A fact marked
*unverified* was not confirmed on a primary source.

## The short version

- **The listing is shared with the iPad.** The record *Gamma - PDF Reader*
  (Apple ID 6814705181) belongs to team `A436A36DDC`, bundle id
  `net.blitzbuild.gamma`, one listing for both platforms. The iPad's current
  release there is 1.1 (3), from the collaborator's fork, whose
  `ipad/project.yml` names that bundle id; this repository's names
  `net.gammapdf.ipad`. Mac 1.0 (2) was accepted as valid and waits for
  review, to be released once approved.
- **Most of what Apple asks is configuration.** A separate packaging
  config on Electron's Mac App Store runtime, the App Sandbox for every
  binary, the store's certificates and profile, no updater and no update
  notices, no login item, a microphone purpose string, a 1024 px icon,
  files the installed app can read. The handoff had all of it, mostly
  behind hard-coded local paths; the repository took it as parameters.
- **The hard part is folders outside the sandbox.** The desktop app writes
  where the user chooses (the storage folder, folders kept on disk), and
  the writer is the local server, a child process. The handoff solved it
  with a C addon, bookmarks handed to the server over stdin, and two new
  backend modules of 1,200 lines with guards in five more. The repository relies instead on
  the child inheriting what the app holds when the child starts, and
  starts a server again after a pick. That keeps the backend untouched,
  but rests on an assumption a signed development build has to confirm.
- **The icon was the one rejection.** Build 1 failed ITMS-90236: the
  `.icns` lacked the 512@2x image, because `desktop/assets/icon.png` was
  512 px. The brand generator now renders it at 1024 px, the same picture
  the handoff rendered by hand.

## Apple's rules as they bear on Gamma

App Review's 2.4.5 lists what a Mac App Store app must do beyond the
general guidelines; 2.5.2 and 5.1.1 add the rest. The ones that shaped
the build:

- **Sandboxed (2.4.5(i)).** Every executable, the frozen Python server
  included. A helper that is to share the app's sandbox carries
  `com.apple.security.inherit` and nothing else; giving it entitlements of
  its own would make it a second sandboxed app. No exception for the home
  folder and no Full Disk Access was needed or asked for.
- **Updates only through the store (2.4.5(vii)).** That covers more than
  electron-updater: Settings → Server compares the build with the newest
  GitHub release and links to it, and an admin notice says so. A store
  install points at no other way to update, so its servers do not check.
- **No code outside the review (2.4.5(iv), 2.5.2).** The launcher's
  *run local servers from a source checkout* settings start any Python the
  user names; the store build ignores them.
- **Nothing at login without consent, nothing left running after quit
  (2.4.5(iii)).** The login item needs a helper a sandboxed app registers
  itself; the handoff left it out and so does the repository. The servers
  stop when the app quits, as on every platform.
- **Purpose strings (5.1.1).** Electron's own `Info.plist` carries a
  generic microphone string (*unverified*); a specific one is safer at
  review.
- **Payments (3.1.1, 3.1.3(f)).** A free app that is a client of a paid
  web service may not sell or point at a purchase. Gamma's servers never
  speak of money (plans are sold on account.gammapdf.com only,
  [dev/billing.md](../dev/billing.md)), and the web app has no call to buy.

App Store Connect's own checks add the version rules (a marketing version
higher than the last approved, a build number higher than every upload),
the icon, and the installer's file modes. The handoff found the last one
the hard way: its build machine's private umask packed 0600 files and
0700 directories, which the store installs as they are, owned by root.

## What was submitted

From the handoff's notes and patch (37 files against v0.3.8):

- `electron-builder.mas.cjs` and `electron-builder.mas-dev.cjs`, the
  `mas` and `mas-dev` targets with the sandbox entitlements, the profile,
  the team id, and release values (version, build, source commit)
  defaulted in the file.
- `mas/main.cjs`, a separate entry point that refuses to run outside the
  Mac App Store runtime or its container and renames the userData folder.
- **Folder access.** The app asks the native picker for security-scoped
  bookmarks and keeps them in a private, mode-checked store
  (`lib/masAccess.js`, 350 lines). A C N-API addon (`lib/masNative.c`)
  resolves each bookmark through CoreFoundation and makes a fresh
  bookmark without security-scope options for the server, which
  `backend_entry.py` reads from stdin before Gamma is imported. The
  server resolves those through ctypes and holds the scopes itself
  (`gamma/mas_scopes.py`). `gamma/mas_fs.py` and changes to `db.py`,
  `storage.py`, `gamma_sync.py`, `folder_links.py` and `app.py` check a
  path and a grant generation around every database connection, storage
  write and sync round. A separate frozen server (`dist-backend-mas`,
  with a protocol marker the packaging checks) carries that code.
- **Lifecycle.** Barriers in `lib/sidecar.js` that block new starts while
  grants change, wait for every server to exit before a grant is released
  or the storage folder moves, and hold the app's quit until then.
- A private inbox that copies a PDF opened from the Finder before it is
  uploaded; the updater, the login item and the source-checkout settings
  off; no single-instance lock; stricter IPC sender checks; a folder name
  from the server sanitised before it becomes a directory.

It notes one failure worth keeping. The app's own app-scoped bookmark,
passed to the server and resolved there with security scope, failed with
Cocoa error 259: such a bookmark belongs to the app that made it. And one
observation: with the app holding a folder, a child that inherits the
sandbox read it. On a development-signed build it then proved a storage
folder outside the container, a page created and read there, a folder kept
on disk written and synced, a second folder chosen while a server ran (the
server restarted, the action finished), a quit that ended the server, and
a relaunch that needed no picker.

## Folder access: the design taken

The server never asks the user for a folder; the app does, and then the
server writes there. In the App Sandbox the pick gives the app access
until it quits, and a bookmark gives it back later. Two ways lead from
the app's access to the server's:

1. **Hand the server a bookmark** (the handoff). The server holds its own
   access, so it could in principle gain a folder while it runs. It needs
   native code on both sides, a private channel, and, to fail closed, the
   backend has to check every operation itself.
2. **Start the server after the app holds the folder.** A child with
   `com.apple.security.inherit` inherits the app's sandbox as it is when
   the child starts, the folders the app has open included (*unverified*
   on a primary source; it is what the handoff observed). A folder picked
   later reaches the server once it starts again.

The handoff's servers restart on a new pick anyway, so the first way buys
no fewer restarts in practice. The second needs no native addon to build
and sign in the release, no backend changes, and keeps the desktop's rule
that the shell treats Gamma as a black box (no MAS mode in `backend/`).
Its cost is the restart: the server about to write into a new place stops
and starts on its port, which the page it shows rides out, and which
happens only when the pick is not inside a folder the server already had.
`lib/folderAccess.js` numbers the grants, each running server remembers
the number it started with, and `reachFolder` restarts only a server older
than the grant it needs.

The repository took the second way. If a signed development build shows a
server unable to read a folder the app holds
([checklist.md](../../desktop/docs/checklist.md) section 9), the first way
is the fallback, and its smallest form is the handoff's addon in the app
and its resolver in `backend_entry.py`, which is the desktop's code, not
Gamma's.

## What else was left out, and why

- **`mas/main.cjs`.** electron-builder always packs the Mac App Store
  runtime for the `mas` targets, and the default userData name keeps the
  documented paths true.
- **A separate frozen server.** With no server change the dmg's serves.
- **The PDF inbox.** The app reads an opened PDF itself
  (`fs.openAsBlob`, then an upload), with the access the open gives it for
  the run.
- **The IPC sender checks and the folder-name sanitising.** Not specific
  to the store, and worth a look of their own: a server folder's name
  goes into the path as it is, so one named `..` would land beside the
  picked directory, on every platform.
- **The store-only lifecycle barriers.** Replaced by `sidecar.stop`
  resolving when the process has exited, which every build now awaits
  before it moves or deletes a server's data.
- **Hard-coded values.** The bundle id and team id are constants of the
  config; versions and the profile come from the environment, and the
  certificates from the keychain (`CSC_NAME` to pick one).

## Still open

- A signed development run of section 9 of the desktop checklist, the
  inheritance first.
- A login item: Electron's `setLoginItemSettings` with
  `type: 'mainAppService'` (SMAppService, macOS 13 and later) may work in
  the sandbox; not tried.
- Intel Macs: the frozen server is built for the build machine's
  architecture, so a universal app needs two servers.
- Bookmarks are never pruned: a folder no longer kept stays in the
  registry's `access`.
- The build is made by hand; the Mac mini that builds the iPad's TestFlight
  releases could make it on the same marker.
- The dmg's `entitlements.mac.plist` has no
  `com.apple.security.device.audio-input`, which a Developer ID build with
  the hardened runtime needs for the microphone.
- Whose team the listing stays under, given the two iPad bundle ids.
