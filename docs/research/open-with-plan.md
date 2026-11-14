# Open with Gamma: the build plan

Status: **built** (2026-11-13; the differences are in the research note's
"As built" section, [open-with.md](open-with.md)). Written 2026-11-13 against `dev` @ 1a806002
after three surveys (what each target can register, what opening should do,
what the plan of [file-explorer.md](file-explorer.md) allows), with their main
claims checked in the code; the file:line references below were read then,
so check them again before building. It decides everything; the research
note beside it, [open-with.md](open-with.md), records why. Revised the same
day after a code review and a design review (the "Critique log" at the end
says what each point changed), and after the owner's answers of the same
day: keep `.typ`, and a Markdown file outside every vault asks whether to
edit it in Gamma (decision 3, section 3.6.1).

The feature: from the operating system's file manager, (1) a **folder** (and
the empty background of an open folder) → *Open with Gamma*; (2) a **file** →
*Open with* / double click when Gamma was chosen: PDFs as today, Markdown,
LaTeX, BibTeX and Typst sources, and whatever else Gamma can show.

## Decisions in one list

1. **A folder opened with Gamma opens what Gamma already has there, or becomes
   a work folder of this computer's own server.** In order: a vault's
   directory or a directory inside one opens that vault's folder (the
   directory's own folder when it has one); a directory around vaults opens
   the one it holds, or lists them; otherwise it becomes a local work folder
   exactly as *Add a synced vault…* makes one (paused, choosing, its tracking
   dialog open). The verb is the person's ask, so the marker and the dialog
   are allowed (file-explorer.md "Choosing what a vault tracks": "Gamma writes
   no file into a directory it was not asked to"). While a work folder the
   verb made still waits for its files, the verb makes no other one (2.4).
2. **A file opened with Gamma never makes a work folder by itself.** Inside a
   vault it is that vault's page (one replacer per file); a work folder's
   file it does not track opens the tracking dialog with that file ticked,
   and after Save the window lands on its page. Outside every vault, and in a
   vault that does not read it (a kept folder's file that is not its own,
   another account's vault), it is a page under *Opened files*, read where it
   is and read-only, in the viewer of its format. A double click is not an
   ask to mark a directory; the one way a file leads to a work folder is the
   person's answer to decision 3's question.
3. **A Markdown file outside every vault asks: *Edit in Gamma* or *Just view
   it*** (3.6.1). A note is editable only through a work folder, whose pull
   writes Gamma's edits back to the file and settles a change made in
   another editor meanwhile; a lone file has no such round, so Gamma never
   writes to it directly. *Edit in Gamma* makes a work folder of the file's
   project (the repository it is in, else its own directory), exactly as the
   folder verb does (refused places, the broad-place question, one at a
   time), opens its tracking dialog with the file ticked, and after Save
   lands on the note, editable. *Just view it* (the default under the test
   harness) reads it as a text document in the code view (`format: "text"`,
   `ext: ".md"`, language `markdown`), read-only like every other file
   there. No new vault kind, no `.gamma/` beside a lone file
   (synced_vaults.md "a vault of one file writes nothing on disk, ever").
   Such a page is **never added to the library** (*Add to library…* hidden,
   the server refuses): every `.md` in the library is a note, and a text
   document named `.md` would be the one exception (a pulling vault would
   write it back as a file its next scan calls a note). Dropping the file on
   the library imports it as a note.
4. **What is registered is the paper loop's sources**, derived from the format
   registry by one rule held in a shared fixture: format `pdf`, format
   `markdown`, and `text` of language `latex`, `bibtex` or `typst`, less the
   extensions the fixture names as left out (`.ltx .cls .sty`: support files
   nobody opens by themselves, and `.cls` is also Visual Basic's class file).
   Registered: `.pdf .md .markdown .tex .bib .typ`. **The one standard, every
   platform:** registering never changes what a double click opens where an
   app already opens that type; where no app opens it, a double click may
   open Gamma, read-only, which takes nothing from anyone. Everything else
   Gamma can show still opens when the person picks Gamma for it (*Choose
   another app*, a drop on the Dock icon, the command line); only binaries
   and programs are refused.
5. **Targets** (details in section 1): NSIS (per user and per machine) gets
   the file types and a classic folder verb on `Directory` and
   `Directory\Background` (on Windows 11 under *Show more options*); the Store
   package gets the file types only; macOS dmg/zip gets the file types and
   `public.folder` (Dock drop, `open -a`), no Finder context entry; the Linux
   `.deb` keeps PDFs as its only file type and gains a folder entry for KDE's
   Dolphin and Cinnamon's Nemo (plain files: a service menu and a Nemo
   action, which claim no default); the Mac App Store build stays PDF-only.
   The Windows 11 top-level entry, a Store folder verb, a Finder entry and a
   GNOME Files entry are not built (each needs native code or a script the
   user installs).
6. **The shell asks the servers what a path is** through one new read-only
   route, `POST /api/folder-links/where`, on every local server (a stopped
   one started before a path is taken as nobody's), as one stated account
   per server (2.3), and then navigates by ids. No disk path ever goes to a
   remote server or into a URL; the one path a URL carries is a file's path
   relative to its work folder, for the dialog's tick, read and dropped from
   the address at once as `track=` is (3.4).
7. **Refused places**, one rule and one text function for every way a vault
   is made: a drive root, Gamma's data directory (both as today), and now the
   home directory, any directory around it, the operating system's own
   directories (and what is inside the ones that hold only the system), and
   the bare mount and temporary directories (`/run`, `/media`, `/Volumes`…,
   never what is inside them). Desktop, Documents, Downloads, Music,
   Pictures, Videos and a cloud drive's root are allowed after one native
   question, asked by every shell path that makes or moves a vault.
8. **The words** of the shell, the installer and the OS entries are English
   (the desktop shell has no catalog, docs/dev/i18n.md "Not in scope"); every
   new word the web app shows goes through `t()` with its Chinese entry.

## 0. Preconditions and working rules

- **Branch.** The branch `open-with-gamma` (from `dev`) holds this plan;
  build on it in its own worktree, merged back into `dev` when done
  ([dev/README.md](../dev/README.md)). Six units in three waves (section 5)
  touch disjoint files, so they can run in parallel in that one worktree; one
  person (or the orchestrating agent) runs every git command and commits.
- **The fixture first.** Before wave 1, write `tests/shared/openwith.json`
  exactly as in 1.1. Unit B's fixture check
  (`test_every_fixture_is_read_by_both_suites`) needs it to exist the moment
  B adds its `ONE_SIDED` line, and unit D needs it to generate; after that
  unit D owns the file.
- A unit edits **only the files it owns** (section 5). A file another unit
  owns is read, never written; a needed change there goes to whoever runs
  the build.
- Backend code outside pytest (a `python -c` that imports `gamma`, `uvicorn`,
  `manage.py`) gets a throwaway `GAMMA_DATA_DIR` in the same command; syntax
  checks with `python -m py_compile`. Pytest isolates on its own.
- Never start, drive or test against a developer's own servers or `data/`.
  The browser harness and the desktop e2e use free ports and throwaway data
  directories ([dev/debugging.md](../dev/debugging.md)).
- Commands, from the worktree:
  - Backend, in `backend/`: `python -m pytest <files> -q -p no:cacheprovider -n 6 --dist loadfile`
    (the backend's venv).
  - Frontend, in `frontend/`: `node --test tests/<file>.test.mjs …` (or
    `npm test`), `npm run i18n` (catalog check), `npm run i18n -- --sync`.
  - Desktop, in `desktop/`: `node --test test/<file>.test.js …` (or
    `npm test`).
  - Browser groups, in `frontend/`, after one `npm run build`:
    `npm run e2e -- --group vaults,work-folders` (with `GAMMA_E2E_PYTHON`
    naming the backend's venv Python).
  - Desktop e2e, in `desktop/`, after the frontend build: `npm run e2e`.
- No new package is needed.
- Fix in batches, run each unit's checks once at its end
  ([the test policy](../dev/debugging.md#local-changes-test-the-affected-modules)).

## 1. What is registered, per target

### 1.1 The one table

`tests/shared/openwith.json` (new; written by the orchestrator before wave 1,
then owned by unit D) is the one list. Its rule is stated once, in its
`about` and in the backend test that holds it to the registry
(`backend/tests/test_openwith_fixture.py`, new, unit D): for each kind, the
set of extensions of `formats.EXTENSIONS` (backend/gamma/documents/formats.py:53-142)
whose row has the kind's `format` and, for `text`, a `language` in the kind's
`languages`, equals the kind's `exts` plus its `left_out`, the two disjoint;
none is in `BLOCKED_EXTENSIONS` or `SANDBOXED_EXTENSIONS`. A row added to the
registry with language `latex` fails that test until the fixture places it
in `exts` or `left_out`, and the generated files follow (1.6). No MIME type
is kept: the Store's `ContentType` is optional and Windows maps a type by
its extension (a copied MIME type had already drifted from the registry's,
`text/x-tex` against `application/x-tex`).

```json
{
  "about": "What the desktop app registers with the operating system (desktop/docs/architecture.md \"Opening files and folders\"): each kind is the extensions of gamma/documents/formats.py EXTENSIONS whose row has its format (and, for text, one of its languages), less its left_out, listed in the file manager's Open with and never made the default where another app opens the type. Read by backend/tests/test_openwith_fixture.py (the rule against the registry), desktop/scripts/openwith.cjs (the NSIS script, the Store manifest and the Linux folder entries it writes), desktop/electron-builder.cjs (macOS) and desktop/test/openWith.test.js.",
  "left_out_why": "support files nobody opens by themselves from a file manager; .cls is also Visual Basic's class file",
  "kinds": [
    {"kind": "pdf",      "format": "pdf",      "languages": [],         "exts": [".pdf"],             "left_out": [],                       "progid": "Gamma.pdf",      "name": "PDF document"},
    {"kind": "markdown", "format": "markdown", "languages": [],         "exts": [".md", ".markdown"], "left_out": [],                       "progid": "Gamma.markdown", "name": "Markdown document"},
    {"kind": "latex",    "format": "text",     "languages": ["latex"],  "exts": [".tex"],             "left_out": [".ltx", ".cls", ".sty"], "progid": "Gamma.latex",    "name": "LaTeX source"},
    {"kind": "bibtex",   "format": "text",     "languages": ["bibtex"], "exts": [".bib"],             "left_out": [],                       "progid": "Gamma.bibtex",   "name": "BibTeX database"},
    {"kind": "typst",    "format": "text",     "languages": ["typst"],  "exts": [".typ"],             "left_out": [],                       "progid": "Gamma.typst",    "name": "Typst source"}
  ],
  "folder_verb": "Open with &Gamma"
}
```

`Gamma.pdf` keeps its name so an update keeps the default a user gave it.
The `&` marks the accelerator on Windows and in KDE; Nemo's action strips
it (1.5).
Not registered, on purpose (said in the note): plain text, code and data
(`.txt .py .json .csv …`: useful beside their repository, which the folder
verb opens), HTML and SVG (sandboxed; browsers own them), notebooks, office
files and archives (the file pane only), images and media, secrets
(`documents/text.is_secret` names), executables (`BLOCKED_EXTENSIONS`), and no
verb on every file (`*\shell`).

### 1.2 Windows, NSIS installer (per user: HKCU; per machine: HKLM)

`desktop/assets/installer.nsh` becomes a **generated** file (header: "Written
by scripts/openwith.cjs from tests/shared/openwith.json: edit those, then
`npm run openwith`"), included as today (`electron-builder.cjs` `nsis.include`,
:85). Under `SHELL_CONTEXT` (HKCU for a per-user install, HKLM for one for all
users; the assisted installer asks, `perMachine` unset, :82-86):

`!macro customInstall`
- per kind: `Software\Classes\<progid>` default = `<name>`;
  `…\DefaultIcon` = `$appExe,0`; `…\shell\open\command` = `"$appExe" "%1"`.
- per extension: `WriteRegNone Software\Classes\<ext>\OpenWithProgids <progid>`;
  `Software\${APP_ID}\Capabilities\FileAssociations` value `<ext>` = `<progid>`.
  Never the extension key's default value. The standard of decision 4 on
  Windows: an extension with a chosen app keeps it (Windows never changes a
  user choice for an `OpenWithProgids` entry); one with none (`.typ` on most
  machines) either shows *How do you want to open this file?* with Gamma
  offered or opens Gamma; both are accepted, and checklist 4.11 records
  which.
- the capabilities and `RegisteredApplications` values as today
  (installer.nsh:16-19).
- the folder verbs:
  - `Software\Classes\Directory\shell\Gamma`: default = `Open with &Gamma`,
    `Icon` = `$appExe,0`; `…\command` default = `"$appExe" "%V"`.
  - `Software\Classes\Directory\Background\shell\Gamma`: the same.
  - Not `Drive\shell` (a drive root is refused) and not `Folder\shell` (it
    also matches Control Panel, Libraries and zip folders).
- `SHChangeNotify(SHCNE_ASSOCCHANGED)` last, as now.

`!macro customUnInstall`, inside the existing `${ifNot} ${isUpdated}` (so an
update keeps every key and `customInstall` rewrites them with the new
`$appExe`): per extension `DeleteRegValue …\<ext>\OpenWithProgids <progid>`,
then `DeleteRegKey /ifempty …\<ext>\OpenWithProgids` and `DeleteRegKey
/ifempty Software\Classes\<ext>` (on stock Windows, and above all under
HKCU, `.typ`, `.bib`, `.markdown` have no key until the installer makes one:
the uninstaller removes every key it made, and `/ifempty` leaves a key that
holds anything else, `.pdf`'s included); per kind `DeleteRegKey
Software\Classes\<progid>`; `DeleteRegKey Software\Classes\Directory\shell\Gamma`
and `…\Directory\Background\shell\Gamma`; the `RegisteredApplications` value
and `Software\${APP_ID}` as today. Then `SHChangeNotify`.

Rule written in the generated header and in architecture.md: an extension
later dropped from the table gets, in `customInstall` of the version that
drops it, the same three deletions (the value, then both keys `/ifempty`), so
an update leaves nothing behind. The generator holds those in a `dropped`
list (empty now).

Cannot: the folder verb in the Windows 11 first menu (needs an
`IExplorerCommand` COM DLL and a signed sparse package; fallback: *Show more
options* / Shift+F10, and *Add a synced vault…* in the app); several folders
selected run the verb once each, handed over one by one (2.4).

### 1.3 Windows, Store package (MSIX)

`desktop/assets/appx-extensions.xml` becomes **generated** too (same header,
as an XML comment): one `<uap:Extension Category="windows.fileTypeAssociation">`
per kind, `<uap:FileTypeAssociation Name="<kind>">` (lowercase, unique),
`<uap:DisplayName><name></uap:DisplayName>`, `<uap:SupportedFileTypes>` with a
`<uap:FileType>` per extension, with no `ContentType` (optional in the
schema; the PDF's `application/pdf` goes too, which changes nothing Windows
shows: it types a file by its extension). No other change to `appx` in
electron-builder.cjs.

Cannot: any folder verb (a `windows.fileExplorerContextMenus` entry needs the
COM DLL; registry writes from a packaged app are virtualized). Fallback:
*Add a synced vault…*. Windows asks the user before the Store app becomes a
default; it never is by itself.

### 1.4 macOS, dmg/zip

`desktop/electron-builder.cjs` `mac` (:108-123), computed from the table
(`require('./scripts/openwith.cjs')`, which reads the fixture):
- `fileAssociations`: one entry per kind, `{ ext: <exts without the dot>,
  name, role: 'Viewer', rank: 'Alternate' }` (the PDF entry as today, :122).
  electron-builder writes `CFBundleTypeExtensions` (app-builder-lib
  electronMac.js:160-180), the mechanism the PDF already uses.
- `extendInfo.CFBundleDocumentTypes`: one entry `{ CFBundleTypeName: 'Folder',
  CFBundleTypeRole: 'Viewer', LSHandlerRank: 'Alternate', LSItemContentTypes:
  ['public.folder'] }`; electron-builder merges it with the entries above
  (electronMac.js:179-180). A folder dropped on the Dock or app icon, and
  `open -a Gamma <dir>`, arrive as `open-file` with the directory.

The standard of decision 4 on macOS: *Alternate* never outranks an app that
claims the type (TextEdit or Xcode for `.md`, TeXShop or TeXstudio for
`.tex`, BibDesk for `.bib`); on a Mac with no app for `.tex`, `.bib` or
`.typ`, Launch Services picks the only claimant and a double click opens
Gamma read-only, which is accepted (it took that click from no one).
Checklist 4.12 checks both.

Cannot: *Open With* on a folder in Finder (Finder has none for folders), a
Finder context entry (needs a Services provider, a Quick Action the user
installs, or a Finder Sync extension: native code). Fallbacks: the Dock drop,
`open -a Gamma <dir>`, *Add a synced vault…*.

### 1.5 Mac App Store build, Linux `.deb`, AppImage

- **Mac App Store** (`electron-builder.mas.cjs`): stays PDF-only, no folder
  type. It sets `mac.fileAssociations` to the table's PDF entry alone and
  spreads `base.mac.extendInfo` without `CFBundleDocumentTypes`. Why: the
  sandbox gives a file or folder from Finder for one run only, and only the
  picker yields the bookmark a work folder needs (lib/folderAccess.js,
  main.js:856-862); its PDFs are uploaded (`addPdf`). The receiving code is
  still defensive (2.5).
- **Linux `.deb`, file types**: `linux.mimeTypes` stays `['application/pdf']`
  (electron-builder.cjs:133-135), with the comment extended. Why, under the
  standard of decision 4: where no application names a default for a type,
  GLib prefers an application that lists the type exactly over one that
  lists its parent, so listing `text/markdown` or `text/x-tex` can take the
  double click from the text editor that lists `text/plain` (and
  `inode/directory` from the file manager, VS Code's long-standing case).
  That would take a click from someone; unchecked on stock Ubuntu and
  Fedora, it is left out (research note, "Still open"). Fallbacks: *Open
  With → Other Application* lists every app, and `gamma <file or directory>`
  on the command line (`Exec=gamma %U` already takes `file://` URLs).
- **Linux `.deb`, the folder entry**: two plain files, generated from the
  fixture's `folder_verb` by `scripts/openwith.cjs` into `assets/linux/` and
  installed by the package through electron-builder's `deb.fpm` source
  mappings (`'<desktop>/assets/linux/gamma-open.desktop=/usr/share/kio/servicemenus/gamma-open.desktop'`,
  `<desktop>` the absolute directory of electron-builder.cjs,
  the same file to `/usr/share/kservices5/ServiceMenus/gamma-open.desktop`,
  and `'<desktop>/assets/linux/gamma.nemo_action=/usr/share/nemo/actions/gamma.nemo_action'`;
  dpkg removes them with the package). Neither is an application entry, so
  neither can become a default for anything:
  - KDE's Dolphin (KF5 and KF6 read the same keys): `[Desktop Entry]
    Type=Service`, `X-KDE-ServiceTypes=KonqPopupMenu/Plugin`,
    `MimeType=inode/directory;`, `Actions=openGamma;`; `[Desktop Action
    openGamma]` `Name=Open with Gamma`, `Icon=gamma`, `Exec=gamma %F`. It
    shows on a folder and on the background of an open one.
  - Cinnamon's Nemo: `[Nemo Action]` `Name=Open with Gamma`,
    `Exec=gamma %F`, `Icon-Name=gamma`, `Selection=any`, `Extensions=dir;`.
  - GNOME's Files (Ubuntu's default) has no packaged entry: its menu takes
    Python extensions (code) or scripts each user drops into
    `~/.local/share/nautilus/scripts`. Not built.
- **AppImage**: there is no AppImage target (`linux.target` is `deb` only).

### 1.6 The generator and its check

`desktop/scripts/openwith.cjs` (new; `scripts/` is not shipped): exports
`table()` (the fixture's kinds), `nsis()` and `appx()` (the two files' text),
`kdeMenu()` and `nemoAction()` (the Linux folder entries), `debFpm()` (the
three mappings for `linux.deb.fpm`), `macFileAssociations()` and
`MAC_FOLDER_TYPE`, and `dropped` (extensions an earlier version registered,
empty now); run as a script it writes `assets/installer.nsh`,
`assets/appx-extensions.xml`, `assets/linux/gamma-open.desktop` and
`assets/linux/gamma.nemo_action`. `package.json`
`scripts.openwith = "node scripts/openwith.cjs"`. `desktop/test/openWith.test.js`
(new, added to `scripts.test`) asserts:
- the four committed files equal what the generator writes;
- every kind's ProgID starts with `Gamma.`; no `ContentType` in the manifest;
- uninstall deletes exactly what install writes: each key and value of
  `customInstall` has its delete in `customUnInstall`, and each extension's
  `DeleteRegValue` is followed by `DeleteRegKey /ifempty` of
  `<ext>\OpenWithProgids` and then of `<ext>` (and the same three in
  `customInstall` for each of `dropped`, tested with a stubbed list);
- `electron-builder.cjs`'s `linux.deb.fpm` equals `debFpm()` and
  `linux.mimeTypes` is `['application/pdf']`;
- the MAS config's mac associations are the PDF entry alone with no
  `CFBundleDocumentTypes`. The MAS config throws unless its three variables
  are set (electron-builder.mas.cjs:39-45), so the test sets
  `GAMMA_MAS_VERSION=1.0`, `GAMMA_MAS_BUILD=1`,
  `GAMMA_MAS_PROFILE=test.provisionprofile` before requiring it and restores
  them after.

## 2. How the shell receives it

### 2.1 The arguments (`desktop/lib/openFiles.js`)

Replace `pdfPaths` (openFiles.js:17-39) with:

- `launchArgs(argv, defaultApp)`: packaged, `argv.slice(1)`; under
  `defaultApp` (`electron .`: dev, `start.cmd`, the e2e's dev launches at
  test/e2e.js:221, :231, `args: [ROOT, ...files]`), `argv.slice(1)` less its
  **first argument that is not a switch** (the app's own directory), as
  Electron's default_app picks the app: Playwright puts its own switches
  first (playwright-core lib/server/electron/electron.js:132,
  `["--inspect=0", "--remote-debugging-port=0", ...args]`), so the app path
  is not always `argv[1]`. Today it is dropped only because it is no `.pdf`;
  with folders accepted it would become a work folder at every dev start.
- `openedPaths(args, cwd, platform = process.platform)` → `[{ path, dir }]`:
  skips non-strings, empty strings and switches (`-…`); reads `file:` URLs
  (`fileURLToPath`); **on win32 an argument ending in `"` is a path that ended
  in a backslash** (`"%V"` of `D:\` arrives as `D:"`, CommandLineToArgvW's
  `\"`): the quote becomes `\`; resolves against `cwd`; keeps what `stat`
  says is a file (`dir: false`) or a directory (`dir: true`); drops the rest;
  de-duplicates by path. No extension filter: the server decides what it can
  show (any file can arrive by *Choose another app* or a drop).
- `broadPlace(dir, places, platform)` → the label of the place `dir` is
  (`places`: `[{ label, path }]`, compared case-insensitively on win32 and
  darwin, trailing separators ignored), or `''`. Pure.

Tests in `desktop/test/openFiles.test.js`: a file, a directory, a `file://`
directory, `D:"` on win32 (and left alone on linux), a missing path, a
switch, a duplicate, `launchArgs` packaged, under `defaultApp` with the app
path second, and with Playwright's two switches before it
(`[exe, "--inspect=0", "--remote-debugging-port=0", ".", dir]` → `[dir]`
after `openedPaths`), `broadPlace` equal / inside / other / case.

### 2.2 `main.js`: one queue for everything handed over

- At the top, before `requestSingleInstanceLock` (main.js:85), compute
  `const handedOver = openFiles.openedPaths(openFiles.launchArgs(process.argv, process.defaultApp), process.cwd()).map((i) => i.path);`
  and pass `{ paths: handedOver }` as the lock's `additionalData`
  (`app.requestSingleInstanceLock({ paths: handedOver })`).
- `second-instance` (main.js:3515-3519) becomes `(_event, argv, cwd, data)`:
  `paths = Array.isArray(data && data.paths) ? data.paths : launchArgs(argv, false)`
  (an older second start passes no data; a packaged one has no app path);
  `paths.length ? openWithGamma(paths, cwd) : (booted && !argv.includes('--hidden') && showWindow())`.
  Reading the second start's own parsed paths is also robust to Chromium
  reordering argv and adding switches.
- `open-file` (main.js:3522-3525): `event.preventDefault(); openWithGamma([file]);`
  (no filter: a folder or a `.md` is no longer dropped).
- Boot (main.js:3497-3507): `files = [...early.splice(0), ...handedOver]`;
  `hidden` requires `!files.length` (folders count); after `booted = true`,
  `if (files.length) openWithGamma(files)`.
- `openPdfFiles`/`earlyPdfs`/`pdfQueue` (main.js:2294-2322) become
  `openWithGamma(paths, cwd)`/`early`/`openQueue`: before boot the paths wait;
  after, one run per hand-over is chained on the queue; it maps
  `openFiles.openedPaths(paths, cwd)` and handles each item in order with
  `item.dir ? openFolderWithGamma(item.path) : openFileWithGamma(item.path)`,
  each in its own `try`: a failure is an `errorBox` "Could not open <name> in
  Gamma." with the error's text (as now, main.js:2311-2315), and the next item
  goes on. It returns `[{ path, workspace?, page?, folder?, error? }]` (the
  e2e reads it). The test hooks (main.js:3599) export `openWithGamma`,
  `readFile` (renamed from `readPdf`), `whereIs`.
- Each item first `await surfaceWindow()` and waits for `opening` (as
  `readPdf` does, main.js:2351-2352).

### 2.3 Asking the servers: `whereIs(p)`

```
whereIs(p) → { ...answer, srv, at, as }   // at = { origin, api } of the server that answered; as = 'window' | 'shell'
  if no local server runs: await startHost()
  ask(srv):                                           // one server; never throws
    try, with a 10 s timeout:
      if current && current.id === srv.id && content and GET /api/session through the window's session has a user:
        at = { origin: openOrigin(), api: serverApi(content.webContents.session) }, as = 'window'
      else
        at = await localServer(srv.id), as = 'shell'
      answer = POST at.origin + /api/folder-links/where { path: p }, headers { ...JSON_TYPE, ...shellWord(at.origin) }
      on 401 with as === 'window' (signed out between the two calls): once more with as = 'shell'
    catch (a 401/403/404/405 of an older server without the route, a timeout, a server that stopped): log it, answer null
  for each local server of the registry that runs (registry order, the window's first):
    a = ask(srv); if a && a.is !== 'none': return a
  for each local server of the registry that does NOT run:          // before "nobody's" is believed
    try startLocal(srv) (allowedOrigins as startHost does) catch → remember srv.name as unchecked
    a = ask(srv); if a && a.is !== 'none': return a
  return the host's 'none' answer (its refusal included), with srv = host, unchecked: [names]
```

**The account each server is asked as** (one rule):
- The server the window shows, while the window is signed in there: the
  window's session, so the answer (`hidden` included) is for the account
  that will navigate.
- Every other local server: `localServer`, the shell's seeded account in its
  own partition (main.js:750-758), as `vaultPlace` and the sync panel act.
  `openServer` signs the window in with that same account when it opens a
  local server (`autoLoginScript`, main.js:3081-3086), so the answer holds
  for the window that lands there.
- The window's server while the window is signed out there: `localServer`
  too, only to learn what the path is. An answer that would navigate on that
  server (`vault`, `around`, `file_vault`) then stops with `Sign in to
  <server>, then open <name> again.` (as `readPdf` says for a signed-out
  window today, main.js:2373); `none` goes on as 3.5 and 3.6 say, where
  `vaultPlace` and `readFile` choose their own place.

**Stopped servers.** A path is taken as nobody's (a new work folder, a lone
file) only after every local server of the registry was asked: one that is
not running is started first (the cost of a second local server, rare). A
server that does not start is named in the notice that follows (`Gamma could
not check <server>'s synced vaults: it did not start.`) and the open goes
on. A remote server is never asked. Only ids come back to the window.

### 2.4 Several at once

Explorer runs a classic verb once per selected item (up to 15) and each run
is a second start; each becomes one `second-instance` and one run on the
queue, so nothing is dropped (unlike `oneAsk`, main.js:955-966, which the
open flow never uses). Items are handled one after another and the window
ends on the last.

**One new work folder at a time.** The shell keeps `waiting`, the work
folders the verb made in this run of the app (`{ srv, linkId, name }`).
Before `openFolderWithGamma` makes a new one, it asks each of them
(`GET /api/folder-links/{id}` through `localServer`) and drops those no
longer choosing (saved, cancelled, gone). While one remains, no new work
folder is made: the folder gets the notice `Choose the files of “<W>” first,
then open <dir> with Gamma again.` and the run goes on with the next item.
Opening an existing vault's folder is not held back. So twelve folders
selected by mistake write one marker, not twelve, and never reach
`MAX_LINKS`.

### 2.5 The Mac App Store build

`openFolderWithGamma` in MAS skips `whereIs` (its servers reach a directory
only once the picker granted it): `picked = await pickDirectory('Open with
Gamma', 'Choose this folder to let Gamma read it: the App Store version
reaches a folder only once you choose it here.', dir)`; Cancel → nothing;
else `addDirectoryVault(picked)` (whose `reachFolder` restarts the server
with the new bookmark, main.js:1325, and whose preview says when the
directory is a vault already). `pickDirectory(title, message, defaultPath =
app.getPath('documents'))` gains the third argument (main.js:864-867;
`choosePath` keeps the bookmark, :856-862); `addDirectoryVault(dir)` already
skips the picker when given a directory, so the signature change is
`pickDirectory`'s alone. `openFileWithGamma` in MAS: a `.pdf` → `addPdf` as
today; anything else → error `This build of Gamma opens PDFs only.`

## 3. The logic

### 3.1 The server's answer: `POST /api/folder-links/where`

New, `backend/gamma/routers/folder_links.py`, body `{path}` (a `WherePath`
model, `path` 1-400 chars as `OpenFile`), `_from_shell` (routers/folder_links.py:148-156),
no workspace header. Read-only: no round, no page, no write. Implemented as
`folder_links.where_is(path, actor) -> dict` in `backend/gamma/folder_links.py`
(`folder_links.resolve` is taken: it settles a conflict, folder_links.py:2222).

The path, two ways: `p`, absolute (`_absolute`) and normalized
(`os.path.abspath` after `expanduser`), **links not followed**, the path as
the file manager named it; `real = Path(p).resolve()`. It must exist (400
`There is no file or directory <path>.`, the only 400 besides a relative
path). The data directory is no 400: it is a `refusal` (step 4), so every
server answers the same shape. Then, in this order:

1. **In a vault.** Every folder link of `all_links()` whose `kind` is
   `folder` (local **and** remote, unlike `_directory_vault_page`,
   linked_files.py:499), compared by `_within(p, dest_of(link))` (identity
   too); when none holds `p`, by `_within(real, …)` (a link on the Desktop
   to a file inside a vault opens that vault's page). `rel` is taken from
   the path that matched. The deepest match wins (vaults do not overlap, so
   there is at most one). If the actor has no role in its workspace
   (`workspaces.role_of`; for a remote link: `created_by != actor`):
   `{"is": "hidden"}`. Else:
   ```
   {"is": "vault", "dir": bool, "name": leaf,
    "link": {"id", "kind": "folder", "workspace": link.workspace_id, "folder": link.folder_id,
             "remote_url", "work": work_vault(link), "choosing": choosing(link),
             "paused": bool(status.get(PAUSED)), "mine": created_by == actor, "name": the folder's name},
    "rel": vault-relative path with "/" ("" for the vault's directory),
    "never": "" | words,          # the first segment of rel the scan never tracks, and why
    # a directory:
    "folder": id, "exact": bool,  # the directory's folder (vault_dirs), else the nearest ancestor's, else link.folder_id
    # a file:
    "page": "" | id, "trashed": bool, "tracked": bool}
   ```
   - `folder`: vaults.db `vault_dirs` (db.py:682-688, `vault_store.dirs`,
     vault/store.py:308) for `rel`, then each parent of `rel`, compared as
     `_vault_row_page` compares (case-folded on Windows); `exact` when `rel`
     itself had a row or `rel == ""`. For a remote vault these are that
     server's folder ids (the host's rows hold them). New helper
     `folder_links.dir_folder(link, rel) -> (folder_id, exact)`.
   - `page`: the vault's row for `rel` of **any** kind with a page
     (`vault_files`, `page_id != ''`, `was = ''`), via `_vault_row_page`
     widened (2 below); for a local vault, `""` and `trashed: true` when that
     page is in Recently deleted (`_block` finds no root row). For a remote
     vault, the row's page id there, unchecked.
   - `tracked`: `page != ""`, or the vault pushes everything and
     `selection.tracked(dest, rel)` (selection.py:72) and `never == ""`.
   - `never`: `selection.never_reason(root, rel) -> (code, words)`, new,
     which reads each directory on the way down with the dialog's own
     `selection._read(root, parent)` (selection.py:128-166) and takes the
     row of the next segment: so the codes are the tree's exactly, `case`
     included (two names of one directory that differ only in case, which
     depends on the other names there), and `rel` is the unresolved one, so
     a junction or symlink on the way is seen as `link` (`.resolve()` first
     would have followed it). A segment `_read` leaves out (a temporary
     name, `.gammaignore`, a name the scan lists nowhere) is code `unlisted`.
     Codes and words (English): `encoding` `its name is not UTF-8`, `link`
     `<segment> is a link or junction`, `node_modules` `it is inside
     node_modules`, `dot` `it is inside a dot directory`, `name` `Windows
     refuses its name`, `case` `another name there differs from it only in
     case`, `unlisted` `Gamma never lists such a file`. Read only while
     `rel` is not `""`; an unreadable directory (`Offline`) is
     `never: ""` and left to the round.
2. **Around vaults** (a directory only): the folder links whose
   `dest_of(link)` is strictly inside the path and that the actor may see:
   `{"is": "around", "vaults": [{"id", "workspace", "folder", "remote_url",
   "work", "choosing", "mine", "name", "rel"}]}` (`rel` the vault's
   directory relative to the path), by `rel`. File links do not count
   (making a work folder takes them in, `_take_in`).
3. **A vault of one file** (a file only): `_file_vault_page`
   (linked_files.py:525-536) → `{"is": "file_vault", "workspace", "page"}`.
4. Otherwise `{"is": "none", "dir": bool, "name", "refusal": "" | words}`:
   `refusal` is `place_refusal(real, directory=dir)` (3.2): for a directory
   the whole rule (a drive root, home, the data directory, a system place);
   for a file only the data directory (a lone file elsewhere is fine).

### 3.2 Refused places (`backend/gamma/folder_links.py`)

One function says why a directory cannot be a vault's, and every caller
uses its text: new `place_refusal(target: Path, directory: bool = True) ->
str` ("" when allowed). `clean_path` (folder_links.py:320-333) becomes
`text = place_refusal(target); if text: raise LinkError(text)`, so every way
a vault is made or moved (create, relink, preview) and `where_is` say the
same words; `clean_file_path` keeps its own data-directory check (a file).
Never called by `check_vault` (vaults that exist keep running). In this
order, the first that applies:
1. a drive or filesystem root (`target.parent == target`): `Pick a
   directory, not the root of a drive.` (as today);
2. the home directory (`Path.home().resolve()`) or a directory around it
   (`C:\Users`, `/Users`, `/home`): `Pick a project directory: Gamma does not
   sync your whole home directory or a directory around it.`;
3. the data directory, inside it or around it (`_in_data_dir`): today's
   text;
4. a system place: `Pick a project directory: <target> is the operating
   system's.` The places, the current platform's only (an unset variable
   skipped):
   - **the directory and anything inside it**: Windows `%SystemRoot%`,
     `%ProgramFiles%`, `%ProgramFiles(x86)%`, `%ProgramData%`; macOS
     `/System`, `/Library`, `/Applications`, `/usr`, `/bin`, `/sbin`, `/etc`
     (as resolved: `/private/etc`); Linux `/usr`, `/bin`, `/sbin`, `/lib`,
     `/lib32`, `/lib64`, `/libx32`, `/etc`, `/boot`, `/dev`, `/proc`, `/sys`;
   - **the directory itself only** (what is inside is a user's: a mounted
     disk, a share, an app's tree): macOS `/Volumes`, `/private`,
     `/private/tmp`, `/private/var`; Linux `/run`, `/run/media`,
     `/run/user`, `/media`, `/mnt`, `/opt`, `/srv`, `/var`, `/tmp`, `/snap`.
     So `/run/media/u/disk/paper` (udisks2 on Fedora, Arch, openSUSE),
     `/run/user/1000/gvfs/smb-share:…/paper` (GVFS) and `/opt/projects/x`
     stay allowed, and a vault there today can still be relinked.

The order puts home before the data directory on purpose: on every desktop
OS the data directory is inside home (`%APPDATA%`, `~/Library/Application
Support`, `~/.config`), so home and the directories around it were already
refused by rule 3, with words about Gamma's data; rule 2 gives the right
words, and covers a moved data root (`GAMMA_DATA_DIR`, a dev checkout) where
rule 3 does not reach. Not `%APPDATA%`, `%LOCALAPPDATA%` or what is inside
`/private` (temporary directories, the tests' included, live there).

### 3.3 `POST /api/folder-links/open` for any format (`backend/gamma/linked_files.py`)

`open_pdf` (linked_files.py:560-596) becomes `open_file(ws, path, actor)`; the
route (routers/folder_links.py:377-392) calls it and answers
`{workspace, page, created, title, format}`. `OpenFile.path`'s comment says
"a file's full path". Changes, in order of the function:

1. `folder_links.clean_file_path(text, pdf_only=True)` gains the keyword;
   `open_file` passes `pdf_only=False` (absolute, resolved, not in the data
   directory, a file that exists; file vaults keep PDFs only). A name in
   `formats.BLOCKED_EXTENSIONS` → `Gamma does not open programs (<ext>).`
2. `_vault_row_page` (:478-485) drops `kind = 'pdf'`: any row with
   `page_id != ''` and `was = ''` (a paired paper's `.md` and `.pdf` rows name
   one page, so either opens it). `_directory_vault_page` (:488-522):
   - a **remote** folder link that holds the path raises `<path> is kept on
     <host of remote_url>: open it there.` (a backstop; the shell asks
     `where` first) **only when** the actor made the link **and** the vault
     reads that file: its `vault_files` row for `rel` has a page, or it is a
     work folder (`work_vault`) whose selection tracks `rel` with
     `never == ""`. That is the one-replacer rule: only then does the host's
     round push that file. Any other file there (a kept folder's `.tex` it
     does not push, another account's vault) falls through and opens as a
     lone file, as a local kept folder's does (3.6).
   - before the inline round: `never` (3.1) → `Gamma never tracks <path>: <words>.`;
     a **paused** vault → `<path> is in “<folder name>”, which is paused:
     resume it in the sync panel, then open the file again.` (no round runs
     a paused or stopped vault from here).
   - a row whose page is in Recently deleted → `<path> is a file of
     “<folder name>”, whose page is in Recently deleted: restore it there.`
     (never falls through to `_rows_standing`, which would restore a page the
     round trashed).
   - choosing / untracked: as now (:503-510).
3. Reading: `hash_any` (:105-122) for every format; `fmt, _ = formats.format_of(name, head)`.
   - `pdf`: `storage.is_pdf(head)` as `hash_file` does, else
     `Cannot read <path>: not a valid PDF (missing %PDF header)`.
   - `markdown` → stored as `format = "text"` (decision 3); `text`, `image`,
     `audio`, `video` as they are.
   - `binary` → `Gamma cannot show <name>: open it in another app.`
   - non-PDF past `config.vault_keep_bytes()` → `<name> is too large for
     Gamma to show here (over <n> MB).` (a PDF has no limit, as now).
   - `ext = formats.vault_extension(name)` (a NAMES-only file such as
     `Makefile` is `.bin` with format `text`, as a work folder stores it).
4. A page of `ws` that stores those bytes: `page_for_doc` (PDF, as now) or
   `blocks_store.page_for_file(conn, hash, ext)` (blocks_store.py:474).
5. **Every PDF-only spot of the follow path** takes the format, extension
   and size of the page's document (`page_document(page["properties"])`),
   or of the row's stored name where no page is read yet:
   - every `stored_name(…, ".pdf")` (linked_files.py:231, 302, 326, 342,
     370, 545, 886-889, 975);
   - `_take_bytes` (:287-317): `sink.store(Path(path), ext)` (today the
     default `.pdf`, folder_links.py:1923, which would store a `.tex`
     through `store_pdf_stream` and refuse it), `sink.replace(…, format=,
     ext=, size=)`, and `pdf_meta.schedule` (:317) for a PDF only;
   - `LocalSink.replace` (folder_links.py:1941-1956) gains keyword
     `format="pdf", ext=None, size=None` and passes them to
     `replace_attachment` (routers/pages.py:658-662, whose `format` defaults
     to `"pdf"`: a changed `.tex` would become a PDF document); file vaults
     call it as now;
   - `_look` (:333-349): `hash_file` (:341, :349) only for a PDF, else
     `hash_any`, so the follow worker follows a `.tex`; its settle test is
     `gamma_sync.settled(path)` with `%%EOF` for a PDF and `tail=None` for
     any other format (gamma_sync.py:424, as the vault's file class
     settles);
   - `locate` (:965-990): `clean_file_path(path, pdf_only=False)`, the read
     of step 3 (format checked: a located file must have the page's format,
     else `Pick a <format name> file: this page reads one.`);
   - `_rows_standing`, `_reopen`, `_catch_up`, `follow`, the carried rows:
     the extension as above.
   A replacement keeps the document's format. On the shell's side,
   `relocateFile` (main.js:1998-2003) picks with `pickFile(title, message,
   ext)` (new beside `pickPdf`: `filters` of that one extension, PDF's
   unchanged) instead of `pickPdf`.
6. New page: `document(hash, name=name, at="path", format=fmt, ext=ext,
   size=size)`, `_put_row(…, ext=ext)`, title the file's stem as now,
   `pdf_meta.schedule` for a PDF only.
7. `reader()` (:1006-1022) adds `format` and `ext` of the page's document
   (the client hides what a non-PDF or a Markdown page cannot do, 3.7).
8. Module docstring (:1-49): "Files read where they are"; *Opened files*
   holds any format Gamma can show; the follow worker follows each.

**What an opened page that is not a PDF cannot do**, refused by the server
and hidden by the client (3.7):
- *Add to library…* (`add_to_library`, :648-700): stores any extension
  (`store_linked`, :611-629), but refuses `keep_pushing` for a non-PDF
  (`Only a PDF keeps pushing from its file.`: file vaults are PDF-only), and
  refuses a text document whose `ext` is `.md` or `.markdown` outright
  (`A Markdown file joins the library as a note: drop it on the library, or
  open its folder with Gamma to edit it where it is.`), decision 3.
- Carrying to another server: `GET` and `POST
  /api/folder-links/opened/{page_id}/carried` (routers/folder_links.py:415,
  :432) answer 400 `Only a PDF is carried to another server.` for a
  non-PDF, and the shell's carry (main.js:2504-) checks the page's
  `doc.format` before asking anything.

### 3.4 The dialog's tick (no server state)

The file to tick travels **in the address**, the way the link does:
`?ws=&folder=&track=<link id>&tick=<rel>` (`rel` the file's path relative to
the work folder, `/`-separated, URL-encoded by `URLSearchParams`). The web
app reads `tick` with `track` at boot and deletes both from the address at
once (useVault.js:120-128 does so for `track`), so it is not kept in
history or copied with a link. The rule of architecture.md:256-259 is about
disk paths: a vault-relative path is what the page and the server exchange
already (`PUT …/selection`'s changes, the dialog's tree), and it goes only
to the local server whose dialog it is (for a remote work folder, the
host's window). The shell checks `never` before (3.6); the dialog checks
again against its own listing.

So there is no focus route, no `state` key (a round writes back the whole
`state` it read when it began, folder_links.py:2540, :2598, which would
drop a focus posted meanwhile), no expiry and nothing to clear on Save or
Cancel.

**Landing after Save** needs one small read: `GET
/api/folder-links/{link_id}/page?path=<rel>` (`_chooser`'s rules, unit A)
→ `{"page": "" | id}` from the vault's row for `rel` (`_vault_row_page`
widened, 3.3 step 2; `""` while no round read it yet, and for a page in
Recently deleted). `rel` is a query parameter checked as the selection
routes check theirs (relative, inside, no `..`), never a link text.

### 3.5 A folder (`openFolderWithGamma(dir)`, main.js)

`r = await whereIs(dir)`, then:

| Case | What happens |
|---|---|
| **A work folder's directory or a directory inside it, this server's** (`is: vault`, no `remote_url`, `work`) | `choosing`: mine → `chooseFiles(r.srv.id, link.id)` (main.js:1654-1672) and notice `“<W>” waits for its files to be chosen.`; not mine → error `Only the account that made the work folder “<W>” chooses what it tracks.` Not choosing → the window on `r.srv` (`openServer` when another shows), `openGammaWorkspace(link.workspace, { folder: r.folder })`: the list view (explorer/FolderList.jsx) at that directory. Notices: `never` → `Gamma never tracks <rel>: <words>. “<W>” opened.`; `!exact` → `Nothing in <rel> is tracked yet: “<W>” opened at <nearest>. Choose files… picks what it tracks.` A paused vault opens as it is (its chip says paused); nothing is resumed. |
| **A kept folder's directory or inside it** (`is: vault`, not `work`) | Same navigation; `folder` `root` (a whole-library vault) → `openGammaWorkspace(link.workspace)` (the library). Never raised to a work folder (that is the sync panel's Push). |
| **A vault of another server** (`remote_url`) | The registry's remote server whose origin is `registry.originOf(remote_url)`; none → error `<dir> is synced with <host>: add that server to Gamma to open it here.` A remote work folder still choosing and mine → `chooseFiles(r.srv.id, link.id)` (the host's window, as now). Else `openServer(nas)`, `openGammaWorkspace(link.workspace, { folder: r.folder })`. Ids only go there. |
| **Hidden** | error `<dir> is kept by another account on this computer.` |
| **Around vaults** | one → open it as above, notice `<dir> holds the <work folder | synced vault> “<name>”, which opened: a work folder is never inside or around another.`; several → notice `<dir> holds <n> synced vaults (<names>): open one of their folders with Gamma, or pick it in the sync panel.` and nothing else. |
| **Nothing there, refused** (`refusal`) | error with the refusal (a drive root, home or around it, the data directory, a system place). |
| **Nothing there, a work folder of this run still choosing** (2.4) | notice `Choose the files of “<W>” first, then open <dir> with Gamma again.`; nothing made. |
| **Nothing there** | `newWorkFolder(dir, tick = '')` (new, shared with 3.6.1): the existing `addDirectoryVault(dir)` (main.js:1320-1331: `vaultPlace`, `reachFolder`, `previewVault`, `makeWorkFolder`), which now asks the broad-place question itself (below), and with a `tick` the dialog opens with it (`chooseFiles(srv, link, tick)`); Cancel → nothing. After `makeWorkFolder`, the link goes into `waiting` (2.4) and the notice says `“<name>” is a work folder of <ws> on <server> now: choose the files it tracks.` (plus `unchecked`'s words, 2.3). Every local server was asked before (`whereIs` starts the stopped ones), so a marker the preview finds that no link of that server holds (`adopts(preview)`, main.js:983) is truly an orphan and is adopted as today. A preview's refusal (a marker another vault holds inside) is said as now; a server refusal (`MAX_LINKS`, a clone or Zotero-linked workspace, a gamma-sync directory) is the error. |
| **Any answer that navigates, the window signed out on that server** (2.3) | error `Sign in to <server>, then open <dir> again.` |

**The broad-place question, for every shell path that gives a vault a
directory**: new `broadOk(dir)` (main.js) → `label = openFiles.broadPlace(dir,
knownPlaces())`; no label → true; else the native question (section 4),
true only for *Make it a work folder*. Called by `addDirectoryVault` (the
folder verb, *Add a synced vault…*, and "Sync the folder…" of a file vault's
directory, oneByOne at main.js:1365), by a folder's *Sync with this
computer…* after its picker (main.js:1203), and by the relink picker
(main.js:1748). The server refuses home and system places for all of them
(3.2); the shell asks about the broad ones.

`knownPlaces()` (main.js): `app.getPath` of `desktop`, `documents`,
`downloads`, `music`, `pictures`, `videos` (labels Desktop, Documents,
Downloads, Music, Pictures, Videos), `process.env.OneDrive`,
`OneDriveConsumer`, `OneDriveCommercial` (label OneDrive), and on darwin
`~/Library/Mobile Documents/com~apple~CloudDocs` (label iCloud Drive). Equal
only: `Documents\paper` is no broad place.

Where a new work folder lands is `vaultPlace` unchanged: the window's local
workspace when the account writes there, it is no clone and no one else sees
it, else the account's own there, else the host's personal workspace
(started and signed into, architecture.md "A directory's vault"). A shared
workspace is never asked: the notice names where it went.

### 3.6 A file (`openFileWithGamma(file)`, main.js)

`r = await whereIs(file)`, then:

| Case | What happens |
|---|---|
| In a vault, `never` | error `Gamma never tracks <name>: <words>.` |
| In a vault of this server, `page` | the window on `r.srv`, `openGammaWorkspace(link.workspace, r.page)`; the page's viewer follows its format (`documents/viewers.js`: a note its notes, a PDF the reader, text the code view, a listed file the file pane with *Show in folder*). No new page, nothing copied, no notice. |
| … `trashed` | error `<name> is a file of “<W>”, whose page is in Recently deleted: restore it there.` |
| … a work folder, `choosing` or not `tracked` | mine → `chooseFiles(r.srv.id, link.id, r.rel)` (its new third argument puts `tick: rel` into `openGammaWorkspace`'s `at`, which writes `&tick=`, 3.4), notice `<name> is not tracked by “<W>” yet: tick it and save to open it.`; not mine → error `Only the account that made the work folder “<W>” chooses what it tracks: ask it to track <name>.` |
| … a work folder, `tracked`, no page yet | `paused` → error `“<W>” is paused: resume it in the sync panel, then open <name> again.`; else `POST /open` on `r.at` with `X-Gamma-Workspace: link.workspace` (its inline round, as today) and open the page it answers. |
| … a kept folder, the file not its own | as a lone file (below): the vault does not read it, so it has one reader. |
| In a vault of another server, `page` | that server opened as for a folder, `openGammaWorkspace(link.workspace, r.page)`. |
| … a remote work folder, not tracked, mine | `chooseFiles(host, link.id, r.rel)` (the host's dialog ticks it; its Save uploads the file there, which the dialog says already). |
| … a remote work folder, tracked, no page | error `<name> has not reached <host> yet: it opens there after the work folder's next round.` |
| … a remote kept folder (not a work folder), the file not its own (no row with a page) | as a lone file: the host's round never reads that file (it pushes PDFs and notes at most), so it has one reader; `/open`'s backstop lets it through (3.3 step 2). |
| A vault of one file | its page (`file_vault`), as today. |
| Nothing, a Markdown file (`.md`, `.markdown`) | **the question of 3.6.1**: *Edit in Gamma* → a work folder of its project with the file ticked; *Just view it* → a lone file (next row). |
| Hidden (another account's vault, local or remote), or nothing | **a lone file**: `readFile(file)`; the backstop does not apply (not the actor's link). A hidden Markdown file is never asked about: its directory is another account's vault. |
| Any answer that navigates, the window signed out on that server (2.3) | error `Sign in to <server>, then open <name> again.` |

`readFile(file)` is `readPdf` (main.js:2349-2417) for any format: the MAS and
`openAddsToLibrary` branches apply to PDFs only (`addPdf`); the place is
`openFiles.readerPlace` (the window's local, writable, non-clone workspace;
a shared one asks first, main.js:2381-2390); with the window on a remote, a
clone or a read-only workspace, `openCarried` (by-doc hash) is asked **for a
PDF only**, else the host's personal workspace; signed out, `Sign in to <X>,
then open the file again.` Then `POST /open`, annotations imported for a new
PDF only (as now, :2401-2405), the notice as now, plus for `format` text with
a `.md`/`.markdown` name: ` To edit it as a note, open it with Gamma again
and pick Edit in Gamma.` Then `openGammaWorkspace(ws, page)`.

### 3.6.1 A Markdown file outside every vault (`editOrView(file)`, main.js)

The owner's choice (2026-11-13): a lone Markdown file asks once, each time
it is opened, before anything is made or read. The question makes the
person's ask explicit, which is what file-explorer.md requires before Gamma
writes `.gamma/` into a directory, and the work folder that answers it is
an ordinary one: the note is edited through the vault and pull writes it
back, so the conflict, rename and duplicate rules of
[dev/work_folders.md](../dev/work_folders.md) cover it, and no write path
for a lone file is added.

1. **The project.** `projectDir(file)` (pure, `openFiles.js`): walk up from
   the file's directory to the first directory holding `.git` (a directory,
   or a file as a git worktree has), stopping at the home directory, a
   drive or filesystem root, or a broad place (`broadPlace`); found → that
   directory, else the file's own directory. A repository's root is the
   work folder because work folders are made for repositories
   (file-explorer.md) and vaults never overlap: a work folder at
   `repo/docs/` would later refuse `repo/`.
2. **Can it be one.** `r = await whereIs(project)`. Usable when `r.is ===
   'none'` with no `refusal`. Otherwise, when `project` is not the file's
   directory, the same for the file's directory (a repository around a vault
   elsewhere in it, or home under git, a dotfiles repository, falls back to
   the file's own directory). Neither usable → no question: the file opens
   as a lone file (`readFile`), and the notice adds the reason (`refusal`'s
   words, or `<dir> holds a synced vault, so it cannot be a work folder.`).
   A work folder of this run still choosing (2.4) → no question either: the
   notice `Choose the files of “<W>” first, then open <name> again.` and
   nothing opened.
3. **The question** (native, `ask(opts, VIEW)`, so the test harness answers
   *Just view it* unless a test queued another answer): message `Edit
   <name> in Gamma?`; detail `<name> is in no work folder. Edit in Gamma
   makes <dir> a work folder: Gamma writes a hidden .gamma folder there,
   asks which of its files to track, and saves your edits back to <name>.
   Just view it opens the file read-only.` (with ` <dir> is the repository
   <name> is in.` when `projectDir` found one); buttons `Edit in Gamma`,
   `Just view it`; default *Edit in Gamma*, cancel *Just view it* (Esc or
   closing views).
4. **Edit in Gamma** → `newWorkFolder(dir, rel)` (3.5's "Nothing there", with
   `rel` the file's path relative to `dir`, `/`-separated): the broad-place
   question when `dir` is one (a `.md` loose in Downloads asks a second time,
   naming Downloads), the preview, `makeWorkFolder` in `vaultPlace`, then the
   dialog with the file ticked (3.4, 3.7: the preset already ticks notes, so
   the tick mostly marks and scrolls to it). After Save the window lands on
   the note (3.7 "Landing after Save"), editable; later edits sync both ways.
   Any refusal or Cancel on the way → nothing made, and the file is not
   read either (the person asked to edit, not to view).
5. **Just view it** → `readFile(file)` as above.

No answer is remembered (a "Don't ask again" is left open, research note).
The MAS build never asks (it opens PDFs only, 2.5).

### 3.7 The client (unit C)

- **Opening by ids**: `?ws=&folder=` (a directory's list view), `?ws=&page=`
  (the page at its viewer), `?ws=&folder=&track=` (the dialog): all exist
  (App.jsx:652-664, useVault.js:120-128). New: `&tick=<rel>` beside `track`
  (3.4), read in App.jsx with `initialTrack` and passed to `useVault`, which
  deletes `tick` from the address with `track` and hands it to `trackLink(id,
  tick)`; `tracking` gains `tick`.
- **The dialog's tick** (`explorer/TrackDialog.jsx`, `explorer/trackSelection.js`):
  when `tracking.tick` is set, the dialog opens the directories down to it
  (reading each unread one with `dir`, the tree's lazy rows), scrolls its row
  into view and marks it, and adds `{path: tick, on: true}` to the pending
  changes when the base does not select it already (a pure helper,
  `tickChange(state, entries, tick)`), with the line `Opened from your
  computer: {file} is ticked. Save to track it and open it.` A row with a
  `never` code is not ticked: the line says `{file} cannot be tracked:
  {why}` with the tree's own words. A path the listing does not have (gone
  since) → `{file} is not in this work folder any more.` For a fresh
  (choosing) vault it works the same over the preset.
- **Landing after Save** (App.jsx:14466-14476 `onSaved`): when the dialog had
  a tick and the vault is this server's, the dialog's state at Save decides
  first: the tick's row unticked → status `{file} was left unticked, so it is
  not a page of this work folder.` and nothing else. Ticked → after
  `watchRound`, `GET /api/folder-links/{id}/page?path=<tick>` (3.4): a page →
  open it; `""` → status `{file} is tracked; its page opens once the work
  folder has read it.` A remote work folder's dialog hands back as now
  (trackedFromPage) and lands on its folder there.
- **Opened files of any format** (`vault/vaultState.js`): the banner words for
  a document that is not a PDF: `This file is read from {path}, not stored in
  the library`, `This file is read where it lies on this computer, not stored
  in the library`, `The file this page was read from is gone` (the PDF words
  stay for PDFs, vaultState.js:69-72). `isOpenedPdf` (:268) is already
  format-agnostic: rename it `isOpenedFile` (App.jsx:248, 1732; the comment
  at libraryUtils.js:297 is unit B's, which writes the new name).
- **What a non-PDF opened page cannot do** (3.3), from `reader.format` and
  `reader.ext` (the open page) or the row's `_doc` (a library row), two pure
  helpers in vaultState.js: `opensAsPdf(doc)` and `openedNote(doc)` (text,
  `.md`/`.markdown`):
  - `carriesToServer(state)` (:251-254) also requires `reader.format ===
    "pdf"`; App.jsx's `carriesAway` (:1745) also requires the row's
    `_doc.format === "pdf"` for any id, not only the focused one;
  - *Keep pushing from the file* (FolderPickerDialog.jsx:109-112) shows for a
    PDF only (a new `pdf` prop); for a non-PDF its place says nothing;
  - *Add to library…* hidden for an `openedNote` (VaultChip.jsx:93-99 and
    App.jsx's row menus :14897, :15052), with VaultChip's hint `To edit it
    as a note, open it with Gamma again and pick Edit in Gamma; to keep a
    copy as a note, drop the file on the library.`;
  - the words: VaultChip.jsx:96, App.jsx:14897, :15052 and
    FolderPickerDialog.jsx:119 say "Store the PDF…" for a PDF and `Store the
    file in the library and file the page in a folder: its notes and chat
    come with it` for any other format (FolderPickerDialog's carry is PDF
    only, so its words stay). VaultChip.jsx:108 is a work folder's PDF
    copy (`file.pdf`), unchanged.
- **Markdown in the code view** (unit B): `codeLanguages.js` `CODE_LANGUAGE`
  gains `markdown: "Markdown"`; `formats.js` `languageOf` (and
  `formats.py` `language_of`, :269-276) answers `markdown` for a Markdown row,
  so a text document stored as `.md` has language `markdown`
  (`page_document`, model.py:229; `pageDocument.js:104`); `LANGUAGE_NAMES` and
  `libraryUtils.js` `LANGUAGE_LABELS` gain `markdown: "Markdown"`. The text
  index's Markdown outline (`documents/text.py` `_RULES`, :577) then applies.
  `FORMAT_VIEWER` is unchanged: no document of format `markdown` is ever
  stored.

### 3.8 The place, sign-in, sharing, several accounts

- A vault's own workspace wins, with no question (3.5, 3.6 vault rows).
- A lone file: `readerPlace`, a shared workspace asks (*Open in <mine>* /
  *Open in <ws>* / Cancel), as for PDFs today.
- A new work folder: `vaultPlace`, a shared workspace never asked.
- Which account each server is asked as: 2.3, one rule (the window's
  session on the server it shows while signed in there, else the shell's
  seeded account through `localServer`, the account `openServer` signs the
  window in with).
- Signed out on the window's local server: `whereIs` asks that server as the
  shell's account only to learn what the path is; an answer that would
  navigate there stops with `Sign in to <server>, then open <name> again.`;
  `none` goes on, `vaultPlace` using the host's own session as today and a
  lone file saying `Sign in to …` as now.
- The window on a remote: `whereIs` asks local servers only; the window moves
  to the local server that keeps the answer, or to the remote that holds the
  vault's folder (by ids).
- Another account of the same local server: `hidden` for folders (making
  one there is refused by `_apart` anyway); a lone file for files (today's
  behaviour).

### 3.9 What leaves the computer

Nothing new. A folder becomes a vault of this computer's own server: read in
place, nothing copied. A lone file is read in place on a local server. A
remote server receives only navigation by ids it gave (its folder and page
ids from the host's rows), and for a PDF opened while it shows, the file's
hash (`openCarried`, unchanged). `whereIs` never asks a remote server
(main.test checks it: no request reaches the stubbed remote while the
window shows it). The dialog's `tick` (a path relative to a work folder)
goes only to a local server's page. The one upload is a file ticked in a
**remote** work folder's dialog, which the dialog already announces. The
chat's rule (a file's text goes to the model provider when it is in context)
is unchanged; a secret's name keeps its text out of the index, the chat and
the chips (`text.is_secret`), Opened files included.

## 4. The words

| Where | Text |
|---|---|
| Windows folder verb | `Open with &Gamma` (accelerator G), the app's icon |
| Windows file types (ProgID names) | `PDF document`, `Markdown document`, `LaTeX source`, `BibTeX database`, `Typst source` |
| Store file types | the same names (`uap:DisplayName`) |
| macOS types | the same names; the folder type `Folder` |
| Linux folder entry (Dolphin, Nemo) | `Open with Gamma`, the app's icon |
| Default apps description | unchanged: `Annotate PDFs and keep notes beside them.` |
| Broad-place question (native) | message `Make all of <label> a work folder?`; detail `<dir> is your <label> folder. Gamma would write a hidden .gamma folder there, read the folder while it runs, and ask which of its files to track. For one project, open that project's folder instead.`; buttons `Make it a work folder`, `Cancel` (default and cancel: Cancel; the test harness answers Cancel) |
| Lone Markdown question (native, 3.6.1) | message `Edit <name> in Gamma?`; detail `<name> is in no work folder. Edit in Gamma makes <dir> a work folder: Gamma writes a hidden .gamma folder there, asks which of its files to track, and saves your edits back to <name>. Just view it opens the file read-only.`, plus ` <dir> is the repository <name> is in.` for a repository; buttons `Edit in Gamma`, `Just view it` (default *Edit in Gamma*, cancel *Just view it*; the test harness answers *Just view it*) |
| Shell notices and errors | as written in 3.5, 3.6 and 3.6.1 (English; the shell has no catalog) |
| Server refusals | 3.2, 3.3 (English, as every backend error) |
| Web app (via `t()`, Chinese in `frontend/src/shared/i18n/locales/zh.json`) | `Opened from your computer: {file} is ticked. Save to track it and open it.` · `{file} cannot be tracked: {why}` · `{file} is not in this work folder any more.` · `{file} was left unticked, so it is not a page of this work folder.` · `{file} is tracked; its page opens once the work folder has read it.` · `This file is read from {path}, not stored in the library` · `This file is read where it lies on this computer, not stored in the library` · `The file this page was read from is gone` · `Store the file in the library and file the page in a folder: its notes and chat come with it` · `To edit it as a note, open it with Gamma again and pick Edit in Gamma; to keep a copy as a note, drop the file on the library.` |

The installer is not localized (NSIS `LangString` unused) and the Store
package is `en-US`: the OS entries are English, said in architecture.md.

## 5. Implementation units

Six units, disjoint files, three waves. Each unit: makes its changes, runs its
own checks once, updates its docs, and reports files changed, checks run and
any gap.

### Wave 1 (A, B, D in parallel)

#### Unit A: the server's paths (backend)

Files: `backend/gamma/folder_links.py`, `backend/gamma/routers/folder_links.py`,
`backend/gamma/linked_files.py`, `backend/gamma/work_folders/selection.py`,
`backend/tests/test_linked_files.py`, `backend/tests/test_track_routes.py`,
`backend/tests/test_vault_routes.py`, `backend/tests/test_folder_links.py`,
`backend/tests/test_remote_work_folders.py`, `backend/tests/test_open_with.py`
(new), `docs/dev/synced_vaults.md`, `docs/dev/api.md`.

Changes: 3.1 (`where_is` and its route, `dir_folder`,
`selection.never_reason` over `_read`), 3.2 (`place_refusal`, used by
`clean_path` and `where_is`), 3.3 (`open_file`, every PDF-only spot of the
follow path, `LocalSink.store`/`replace` taking the format, `reader()`'s
`format` and `ext`, the backstop, `add_to_library`'s two refusals, the
carried routes' PDF-only refusal), 3.4 (`GET /{id}/page?path=`).

Tests:
- `test_open_with.py`: `where` of a work folder's root, a directory with a
  row, one without (nearest, `exact` false), a never-tracked segment (dot,
  node_modules, `case`: `Notes.md` beside `notes.md`), a **junction or
  symlink inside a work folder** (`never` code `link`; skipped where the
  platform cannot make one), a link outside every vault to a file inside one
  (that vault's page, matched on the resolved path), a file tracked (page),
  untracked, trashed page, a choosing vault, a paused vault, a kept folder
  (root and file not its own), around one and two vaults (file links not
  counted), a file vault, another account's vault (`hidden`), a missing path
  (400), and as `refusal` with `is: none` (never a 400): the data
  directory, a drive root, home (monkeypatched `Path.home`), a directory
  around home; the page route (`/{id}/page?path=`: a page, `""` before the
  round, `..` 400, another editor 403).
- `test_folder_links.py`: `place_refusal`'s order and words (root, home with
  `Path.home` monkeypatched, around home, data directory, a system place of
  the platform); `clean_path` still takes a directory inside home and, on
  Linux (or with the platform's list monkeypatched), `/run/media/u/disk/paper`
  and `/opt/projects/x`, refusing `/run` and `/opt` themselves;
  create/preview/relink say the same words.
- `test_linked_files.py`: open a `.tex`, a `.md` (`format` text, `ext` `.md`),
  a PNG, a WAV; `reader()` gives `format` and `ext`; reopen, and the follow
  worker's replacement of a changed `.tex` (the page's document stays
  `text`/`.tex`, stored through `store_file_stream`, no `pdf_meta`);
  `locate` of a moved `.md` (and a located file of another format refused);
  a library page holding the same bytes (`page_for_file`); refusals: binary,
  blocked, too large (keep limit monkeypatched); a PDF unchanged,
  `pdf_meta.schedule` for a PDF only; `add_to_library` of an opened `.tex`
  (stored), of a `.tex` with `keep_pushing` (refused), of an opened `.md`
  (refused); the carried route refuses a `.tex`.
- `test_track_routes.py`: a tracked `.md` and `.tex` of a work folder open
  their pages (the dropped `kind = 'pdf'`); a paused vault refuses with no
  round; a never-tracked file refuses with no round; a trashed page refuses.
- `test_vault_routes.py`: a loose `.tex` in a pull-only vault opens as a
  lone file.
- `test_remote_work_folders.py`: `where` of a remote work folder's directory
  and file answers that server's ids; `/open` of a file it tracks is refused
  (`kept on`), never a local page; `/open` of a `.tex` in a remote kept
  folder that pushes PDFs and notes only (no row) opens as a lone file; the
  same file of another account's remote vault opens as a lone file.

Command: `python -m pytest tests/test_open_with.py tests/test_linked_files.py tests/test_track_routes.py tests/test_vault_routes.py tests/test_folder_links.py tests/test_remote_work_folders.py tests/test_work_folder_paths.py -q -p no:cacheprovider -n 6 --dist loadfile`

Docs: `docs/dev/synced_vaults.md` "Opened files" (:361-376; and :21-25,
:273-275 "PDFs' alone"): any format, read in place, the refusals, the format
of a lone Markdown file, the follow worker per format; the refused places with
the existing refusals; "Which path is what" (`where`) as a short section.
`docs/dev/api.md` (:1211): `POST /api/folder-links/where`,
`GET /api/folder-links/{id}/page?path=`, `/open`'s new answer field and
refusals, `add_to_library`'s and the carried routes' new refusals, the
opened page's `reader.format`/`ext`.

#### Unit B: the registry's Markdown language and the code view (twins)

Files: `backend/gamma/documents/formats.py`, `frontend/src/documents/formats.js`,
`frontend/src/documents/codeLanguages.js`, `frontend/src/library/libraryUtils.js`,
`tests/shared/formats.json`, `tests/shared/document.json`,
`backend/tests/test_shared_fixtures.py`, `frontend/tests/documents.test.mjs`,
`frontend/tests/viewers.test.mjs`, `frontend/tests/libraryUtils.test.mjs`,
`docs/dev/documents.md`.

Changes: 3.7 "Markdown in the code view": `language_of` / `languageOf`
answer `markdown` for a row of format `markdown` (NAMES first, as now);
`format_of`/`formatOf` unchanged (a `.md`'s format and language stay
`markdown`/null: the fixture's `names` rows do not move). `LANGUAGE_NAMES`
and `LANGUAGE_LABELS` gain `markdown: "Markdown"`, the fixture's
`language_names` too, its `about` saying "every language a text document can
have: the tables' and markdown (a Markdown file read as text outside a
vault)". `CODE_LANGUAGE.markdown = "Markdown"` (language-data's name).
`document.json` gains a case "a Markdown file read in place as text (an
opened file outside any vault)": `{format: text, name: README.md, hash: d5,
ext: .md, at: path}` → view `language: "markdown"`. In
`test_shared_fixtures.py`: `test_language_names` adds `markdown` to the
languages of the tables (`| {"markdown"}`), and `ONE_SIDED` gains
`"openwith.json": "what the desktop app registers with the operating system;
its other reader is desktop/test/openWith.test.js, which this check does not
scan"` (the orchestrator wrote the fixture before wave 1, section 0). In
`libraryUtils.js`, the comment at :297 names `isOpenedFile` (unit C's rename).

Tests: `test_shared_fixtures.py` (formats and document fixtures, language
names, every fixture read), `documents.test.mjs`, `viewers.test.mjs` (`codeLanguageName` of that
document is `Markdown`, and every `CODE_LANGUAGE` value is a language-data
name as now), `libraryUtils.test.mjs`.
Commands: backend `python -m pytest tests/test_shared_fixtures.py -q -p no:cacheprovider`;
frontend `node --test tests/documents.test.mjs tests/viewers.test.mjs tests/libraryUtils.test.mjs`.

Docs: `docs/dev/documents.md` "Which viewer a page shows" (:629-633: a
Markdown file outside a vault is a text document in the code view, language
markdown; a note still has no document pane), the formats section (the
language rule), and "Left for later" (:1001-1002): strike "*Opened files* stay
PDFs" (they take every format with a viewer; carrying to another server stays
PDF-only).

#### Unit D: registration (desktop build)

Files: `tests/shared/openwith.json` (after the orchestrator wrote it),
`backend/tests/test_openwith_fixture.py` (new), `desktop/scripts/openwith.cjs`
(new), `desktop/assets/installer.nsh` (generated),
`desktop/assets/appx-extensions.xml` (generated),
`desktop/assets/linux/gamma-open.desktop` and
`desktop/assets/linux/gamma.nemo_action` (new, generated),
`desktop/electron-builder.cjs`, `desktop/electron-builder.mas.cjs`,
`desktop/package.json`, `desktop/test/openWith.test.js` (new),
`desktop/docs/release.md`, `desktop/docs/checklist.md`.

Changes: section 1. In `electron-builder.cjs`: the header comment (:37-44)
says "Files and folders" and names the generator; `mac.fileAssociations` and
`mac.extendInfo.CFBundleDocumentTypes` from the generator; `linux.mimeTypes`'
comment says why it stays PDF-only; `linux` gains `deb: { fpm: debFpm() }`
(the two Linux folder entries, with absolute source paths built from
`__dirname`, so fpm finds them whatever its working directory; they are read
at package time only and need not be in the app's files); `nsis.include` and
`appx.customExtensionsPath` unchanged paths. `electron-builder.mas.cjs`:
PDF-only (1.5). `package.json`: `scripts.openwith`, `test/openWith.test.js`
added to `scripts.test`.

Tests: `test_openwith_fixture.py` (the rule both ways against
`formats.EXTENSIONS`: `exts ∪ left_out` equals the registry's set and the
two are disjoint; nothing blocked or sandboxed; ProgIDs unique; kinds unique
and lowercase; no `mime` field); `desktop/test/openWith.test.js` (1.6); check
the generated NSIS text by eye for quoting (`'"$appExe" "%V"'`).
Commands: backend `python -m pytest tests/test_openwith_fixture.py -q -p no:cacheprovider`;
desktop `node scripts/openwith.cjs` then `node --test test/openWith.test.js`.
No installer build here (`npm run dist` needs signing-free NSIS and takes
minutes; it is in the manual checklist).

Docs: `desktop/docs/release.md` (:42-46): what each target registers, PDF-only
for MAS and the `.deb`'s file types, the `.deb`'s two folder entries, the
generator. `desktop/docs/checklist.md`: rows 4.11
and 4.12 widened, and new manual rows (5.3 below), plus the auto rows for
unit E's e2e steps by their names (5.2, E).

### Wave 2 (C, E in parallel, after A and B)

#### Unit C: the dialog's tick and Opened files in the web app (frontend)

Files: `frontend/src/explorer/TrackDialog.jsx`, `frontend/src/explorer/trackSelection.js`,
`frontend/src/vault/useVault.js`, `frontend/src/vault/vaultState.js`,
`frontend/src/vault/VaultChip.jsx`, `frontend/src/vault/FolderPickerDialog.jsx`,
`frontend/src/app/App.jsx` (the `tick` parameter beside `initialTrack`,
onSaved, the `isOpenedFile` rename, `carriesAway`, the two row-menu *Add to
library…* entries and their words at :14897 and :15052; nothing else),
`frontend/src/shared/i18n/locales/zh.json`, `frontend/tests/trackSelection.test.mjs`,
`frontend/tests/vaultState.test.mjs`, `frontend/tests/e2e/scenarios/workFolders.mjs`,
`frontend/tests/e2e/scenarios/vaults.mjs`, `docs/dev/work_folders.md`.
(`libraryUtils.js` is unit B's: B writes the renamed comment.)

Changes: 3.7 (the tick, landing, banner words, `isOpenedFile`, what a
non-PDF opened page cannot do: `carriesToServer`, `carriesAway`, keep
pushing, *Add to library…* for an opened Markdown file, the words);
Chinese entries via `npm run i18n -- --sync`, then translated.

Tests: `trackSelection.test.mjs` (`tickChange`: ticks an unselected file,
nothing for a selected one, nothing for a `never` row, the directories to
open), `vaultState.test.mjs` (banner words by format, `carriesToServer`
false for a text page, `opensAsPdf`, `openedNote` for `.md` and
`.markdown` text, not for `.tex`); browser: `workFolders.mjs` a new
scenario "a file opened from the computer is ticked in Choose files… and
opens after Save" (make a work folder through the API as the group does,
load `?ws&folder&track&tick=sub/a.tex`, `tick` and `track` gone from the
address, the row ticked and marked, Save, the window lands on the page; and
once more with the row unticked before Save: the status says it was left
unticked), `vaults.mjs` a scenario "Opened files read a .tex and a .md in
the code view" (`POST /api/folder-links/open` as at :226; the code view
shows, Markdown coloured, the banner says "This file is read from"; the
`.md`'s chip offers no *Add to library…*, the `.tex`'s Add dialog has no
*Keep pushing*).
Commands: `node --test tests/trackSelection.test.mjs tests/vaultState.test.mjs tests/i18n.test.mjs`;
`npm run i18n`; browser groups run in the closing pass.

Docs: `docs/dev/work_folders.md` "The tracking dialog" (:733-800: a third way
in, the tick and the landing) and :1038-1040 ("Opening a file with the app":
the dialog with the file ticked, never-tracked and paused refusals).

#### Unit E: the shell receives and routes (desktop)

Files: `desktop/lib/openFiles.js`, `desktop/main.js`, `desktop/test/openFiles.test.js`,
`desktop/test/main.test.js`, `desktop/test/e2e.js`, `desktop/docs/architecture.md`,
`docs/dev/desktop.md`.

Changes: section 2 and 3.5, 3.6, 3.6.1, 3.8 (shell side): `launchArgs`,
`openedPaths`, `broadPlace`, `projectDir`; the lock's `additionalData`;
`second-instance`, `open-file`, boot; `openWithGamma`/`openQueue`; `whereIs`;
`openFolderWithGamma`, `openFileWithGamma`, `newWorkFolder`, `editOrView`
and its question, `readFile` (renamed `readPdf`),
`knownPlaces`, `broadOk` and its three callers, `waiting` (2.4),
`pickDirectory(…, defaultPath)`, `pickFile` for `relocateFile`,
`chooseFiles(…, tick)` and `openGammaWorkspace`'s `tick`, the carry's
PDF-only check; the notices; the MAS branches; test hooks renamed. *Server
→ Open a PDF…* keeps its PDF picker and calls `openWithGamma([file])`.

Tests:
- `openFiles.test.js`: 2.1; `projectDir`: a file in a repository's
  subdirectory (its root), a `.git` file (a worktree), no repository (the
  file's directory), a repository at home (stops: the file's directory), a
  file in a broad place (its directory).
- `main.test.js`, the lone Markdown question (3.6.1): asked for a `.md` and
  a `.markdown` with nothing there, never for a `.tex`, a vault's file or a
  hidden one; *Just view it* (the harness's answer) reads it with the hint;
  a queued *Edit in Gamma* makes the work folder at the repository's root
  with `tick` the file's path from there (and at the file's directory with
  no repository, and at the file's directory when the root is around a
  vault); no question and a read with the reason when neither can be one;
  no question and nothing opened while a work folder of this run chooses;
  Cancel at the broad-place question makes and reads nothing.
- `main.test.js` (its stubbed servers, :1540-1590): `readFile` places for a
  `.tex` (no `openCarried`, no annotation import), the shared question for a
  `.md`, the Markdown hint in the notice; `whereIs`: the window's server
  through its session and the others through `localServer`, the first
  non-`none` returned; **a signed-out window** (its server asked through
  `localServer`, and a `vault` answer there ends in `Sign in to …` with no
  navigation); a 401 between the session check and the call retried as the
  shell; a server that times out or answers 404 skipped; a stopped local
  server started before a `none` is believed, and one that fails to start
  named in the notice; **no request to the remote** while the window shows
  one (3.9); each row of 3.5 and 3.6 with a stubbed `where` answer (which
  navigation, which notice or error, no `/open` for a page, `chooseFiles`
  with `tick` for an untracked file, a remote kept folder's file read as a
  lone file); `waiting`: a second folder while the first still chooses gets
  the notice and no `POST /api/folder-links`, and is made once the first is
  saved; `broadOk` asked by `addDirectoryVault` for the Desktop (harness
  answers Cancel: nothing made) and not for `Documents/paper`; the queue
  keeps going after a failure; boot `hidden` false with a folder.
- `e2e.js` new steps (dev and packaged):
  1. *a folder opened with Gamma: a second start hands over a directory, which
     becomes a work folder of the workspace the window shows, paused and
     choosing, the window on its tracking dialog; opened again while choosing,
     the dialog comes back* (assert `/api/folder-links` has the link at that
     path, `choosing`, the URL's `track=`; and no link at the app's own
     directory `ROOT`, which the dev launch's Playwright switches put after
     themselves).
  2. *files of a work folder opened with Gamma: after Save a tracked .tex opens
     its page in the code view, a subdirectory opens its folder, an untracked
     .csv opens Choose files… with it ticked* (save through `PUT
     …/selection` with base `preset` and the change `{format: "latex", on:
     true}`, since the preset ticks PDFs and notes only; assert `page=`,
     `folder=`, and for the `.csv` that the shell loaded `track=` with
     `tick=` and the dialog shows its row ticked).
  3. *a Markdown file opened with Gamma outside any vault asks; Just view it
     reads it where it is under Opened files, a text document in the code
     view, and the bar says how to edit it* (the question in `asked`; doc
     `{format: text, ext: .md, at: path}`).
  3b. *Edit in Gamma makes a work folder of the Markdown file's repository,
     the dialog has the file ticked, and after Save the note opens* (a
     directory with `.git` and `docs/notes.md`; `answers` queued with *Edit
     in Gamma*; the link at the repository's root, `tick=docs/notes.md`
     loaded and gone from the address, Save, the window on the note's page,
     an edit there reaching the file after the round).
  4. *a directory named at a cold launch becomes a work folder once the app
     is up* (launch with `args: [ROOT, dir]`: the window shows, not hidden,
     and lands on the dialog: `handedOver` and boot's `hidden`).
  5. The existing PDF steps (:648, :1406) keep passing with the renamed hook
     (`s.openWithGamma([f])`, :668).
Commands: `node --test test/openFiles.test.js test/main.test.js`; the e2e in
the closing pass.

Docs: `desktop/docs/architecture.md` "Opening PDF files" (:799-979) becomes
"Opening files and folders" (what each target registers and cannot, the
queue, `whereIs` and the account each server is asked as, the tables of 3.5
and 3.6, the refusals, the words are English), "A work folder" (:245-260:
the hand-over may add `tick=<rel>`, a path relative to the work folder, read
and dropped from the address at once; still no disk path), "A directory's
vault" (a third way in, the broad-place question in `broadOk`), the File map (:1232, :1266:
openFiles.js, scripts/openwith.cjs). `docs/dev/desktop.md` (:44).

### Wave 3

#### Unit F: the plan and the user guide (docs)

Files: `docs/research/open-with.md` (the research note beside this plan:
its status, and an "As built" paragraph from the units' reports),
`docs/research/README.md` (its row's last column), this plan's status line,
`docs/research/file-explorer.md` (the "Later" row of "In steps" gains
"opening files and folders from the file manager: [open-with.md]"; step 3's
"Not built" item 23 says *Opened files of formats other than PDF* is built
there, and that a Markdown file outside every vault asks whether to become
a note of a work folder, else opens read-only in the code view, and is never
added to the library (a decision of the new note, recorded beside the item;
"Still to decide" 6, Markdown *inside* a repository's work folder, is a
different question and is left as it is),
`docs/user_guide/user_guide.md`
("PDFs read where they are" :486-489 widened to files; "Work folders" :474 a
folder opened with Gamma; the desktop line :660).

Checks: the diff and every relative link (each `](…)` target exists from
its file).

### Closing pass (the orchestrator, after wave 3)

1. Read every unit's report; resolve any cross-unit gap they raised.
2. One frontend build in the worktree (`npm run build`, the worktree's
   `frontend/dist` only).
3. Backend: the union of A's, B's and D's pytest files in one run.
4. Frontend: `npm test` and `npm run i18n`.
5. Desktop: `npm test`, then `npm run e2e` (dev).
6. Browser: `npm run e2e -- --group vaults,work-folders,viewers` with
   `GAMMA_E2E_PYTHON`.
7. `node scripts/openwith.cjs` leaves the generated files unchanged (`git
   diff --stat` read-only).
8. Report checks run, gaps, and the manual rows below as not yet run.

### 5.2 Desktop e2e steps (unit E; listed in checklist.md as auto)

The four new steps of unit E, by their names above, in `checklist.md` rows
4.15 (folders, the cold launch) and 4.16 (files of a work folder and lone
files).

### 5.3 The manual checklist (unit D writes them into desktop/docs/checklist.md)

Installed builds only; none can run in a test:

- **4.11 (widened) Windows installer, per user and per machine:** *Open with*
  on `.md`, `.tex`, `.bib`, `.typ` lists Gamma, as on `.pdf`; Settings →
  Default apps lists Gamma for each; no default changes at install; right-click
  a folder → *Open with Gamma* (Windows 10 in the menu, Windows 11 under
  *Show more options*) makes a work folder or opens its folder; the empty
  background of an open folder does the same for that folder; the background
  of a drive root says it is refused; of the Desktop asks first; three new
  folders selected make one work folder and say for the other two to wait;
  the verb while Gamma runs in the tray; on a machine with no app for `.typ`,
  a double click on a `.typ` either shows *How do you want to open this
  file?* or opens Gamma (record which: both are accepted), and a `.md` or
  `.tex` with an app chosen still opens that app; an update keeps every
  entry and a default the user chose; an uninstall removes the verb, the
  ProgIDs, every *Open with* entry and Default apps, and leaves no empty
  `HKCU\Software\Classes\.typ` (or `.bib`, `.markdown`) key that it made.
- **4.12 (widened) Store package:** the file types as 4.11, no folder verb.
  **macOS dmg:** *Open With* on `.md`/`.tex`/`.bib`/`.typ` lists Gamma; a
  `.md` still opens TextEdit (or the app it opened before), a `.tex` with
  TeXShop or TeXstudio installed still opens it; on a Mac with no app for
  `.typ`, a double click opens Gamma read-only (accepted, decision 4); a
  folder dropped on the Dock icon and `open -a Gamma ~/paper` make or open
  its work folder, cold and while running. **Mac App Store:** still PDFs
  only; a folder dropped (if Finder allows) opens the picker at it. **.deb:**
  `dpkg -c` lists the service menu (both paths) and the Nemo action;
  unchanged for PDFs; in Dolphin (Kubuntu) and Nemo (Mint) a folder's and a
  folder background's context menu has *Open with Gamma*, which makes or
  opens its work folder; `gamma ~/paper` and `gamma notes.md` from a
  terminal open the folder and the file; `xdg-mime query default
  inode/directory` and `text/markdown` and `xdg-open` of a directory and of
  a `.md` are what they were before the install; removing the package
  removes the two entries.
- **4.17** A file opened from a NAS-synced directory opens its page on the
  NAS, never a local copy; with the NAS gone from the registry, the error
  says to add it.
- **4.18** A `.md` double-clicked (Gamma chosen for it) in a repository's
  subdirectory asks *Edit in Gamma* or *Just view it*: Edit makes the
  repository a work folder with the file ticked, and after Save an edit in
  Gamma reaches the file (open it in another editor); View reads it in the
  code view. A `.md` loose in Downloads asks, then asks again about
  Downloads; Cancel there makes nothing.

## 6. Risks and differences from the plan

- **Differences from the file-explorer plan:** none in its decisions. It
  builds what step 3 left out ("Opened files of formats other than PDF") and
  decides a question the plan did not ask (a Markdown file outside every
  vault asks whether to become a note of a work folder, else is read in the
  code view, and is never added to the library); "Still to decide" 6
  (Markdown inside a repository's work folder) is untouched. It
  adds one rule the plan did not state: the home directory, directories
  around it and system directories are refused as a vault's directory. It
  adds a new step (the plan's "Later"), with its own note.
- **A lone Markdown file's work folder (the owner's choice):** the first
  version of this plan opened a lone `.md` read-only only, because making
  its folder a work folder on a double click writes `.gamma/` into a
  directory the person did not name (Downloads, the Desktop), spends the 10
  vaults a workspace may have, and a work folder at `repo/docs/` later
  blocks `repo/`. The owner chose to ask instead (3.6.1), which meets each:
  the answer names the directory, the broad places ask again and home and
  system places are refused, the repository's root is taken over a
  subdirectory, one new work folder at a time holds, and `MAX_LINKS` is the
  server's refusal, said as the error. Left: a person who answers *Edit* for
  many stray files in many directories makes that many work folders.
- **Platforms that cannot:** Windows 11's first menu, the Store's folder
  verb, a Finder entry and a GNOME Files entry need native code or a script
  each user installs (an `IExplorerCommand` DLL with a signed sparse
  package, as VS Code ships; a Finder Sync extension or a Quick Action; a
  nautilus-python extension or a user script). A later step; the research
  note keeps what it would take.
- **Linux defaults:** the `.deb` lists no new file type, and its two folder
  entries are no application entries, so no default can move. If a later
  step lists Markdown or LaTeX, check on stock Ubuntu and Fedora that
  `xdg-mime query default` does not move.
- **The `.deb`'s fpm mappings** are untested until a `.deb` is built: a wrong
  source path fails the Linux job of the release workflow, loudly. If
  electron-builder ever stops passing `deb.fpm` through, the fallback is the
  same two copies in `deb.afterInstall` / `afterRemove` scripts.
- **NSIS quoting:** `"%V"` of a drive root reaches the app as `D:"`; the
  parser repairs it and the server refuses the root. A verb under
  `Directory\Background` of a library or a virtual folder does not appear
  (Windows shows it for file-system folders only).
- **Several local servers:** the overlap check is per server. `whereIs` asks
  every running one, and before it takes a path as nobody's starts the
  stopped ones, so a directory or a file kept by another local server is
  opened there instead of adopted or read twice. The price: opening a lone
  file with a second local server stopped starts that server (seconds, and
  its vaults' rounds). Two servers that both run and both hold a vault there
  (made before this step) are answered by the first.
- **A large directory:** the preview walks the whole directory with no
  budget (`folder_links.preview`, :1057); the broad-place question keeps the
  usual mis-clicks (Desktop, Downloads, Documents) away, the refused places
  keep home and the system away, and the bar shows "Adding the work folder…"
  while it reads. A budget on the preview is left for later (it also finds the
  markers inside, which a cut walk could miss).
- **An Opened page and a later work folder:** a lone file opened under
  *Opened files* and later inside a work folder made of its directory has two
  pages (the Opened one and the work folder's), as a PDF does today; the
  work folder's opens from then on. Taking such pages in when a work folder is
  made is left for later.
- **Turning off the verb:** VS Code offers its context entries as installer
  checkboxes; Gamma's assisted installer has no such page, so they are always
  written and removed with the app. A later installer page could make them
  optional.
- **Opened files of other formats** add rows the follow worker hashes: a large
  log or video read in place is hashed whole on open and on change. The keep
  limit (64 MB by default) refuses non-PDFs past it.

## Critique log

Two reviews (code, design) of the first version, each point checked in the
code before changing the plan. All were right; where a fix differs from the
one proposed, the entry says why. Then the owner's answers.

**The owner's answers (2026-11-13)**
1. `.typ` stays registered (the owner writes Typst).
2. A lone Markdown file: the read-only-only rule was replaced by the
   question of 3.6.1, *Edit in Gamma* making a work folder of the file's
   project with the file ticked; decision 3, 3.6, 3.7's hint, the words,
   unit E's tests, e2e step 3b, checklist 4.18 and section 6 follow.

**Code review**
1. Dev launches made `ROOT` a work folder: confirmed (playwright-core
   electron.js:132 puts `--inspect=0 --remote-debugging-port=0` first).
   `launchArgs` drops the first non-switch argument under `defaultApp`
   (2.1); a test with the switches first.
2. `openwith.json` fails `test_every_fixture_is_read_by_both_suites`:
   confirmed (test_shared_fixtures.py:50-70). The `ONE_SIDED` line goes to
   unit B, which owns the file (it must edit `test_language_names` anyway),
   and the orchestrator writes the fixture before wave 1 so B's check never
   sees the line without the file (section 0, unit B). Not unit D, to keep
   files disjoint.
3. `folder_links.resolve` is taken (:2222): the function is `where_is`, the
   route `POST /api/folder-links/where` (3.1).
4. The focus in `state` is lost to a round's write-back (:2540, :2598) and
   sticks after a Cancel: confirmed. Not kept in memory either: the tick
   travels in the address beside `track` (design 1's fix), so there is no
   server state at all (3.4).
5. PDF-only spots missed: all six confirmed (`_take_bytes`'s `store` and
   `pdf_meta`, `LocalSink.replace`'s `format="pdf"` through
   `replace_attachment`, `_look`'s `hash_file`, `locate`, `relocateFile`'s
   `pickPdf`). All listed in 3.3 step 5; `store`/`replace` take format, ext
   and size from the page's document; Locate picks a file of the page's
   extension.
6. The remote backstop contradicted 3.6: confirmed. It refuses only when
   the actor made the link and the vault reads the file (a row with a page,
   or a work folder that tracks it); the missing table row is added (3.3
   step 2, 3.6).
7. `whereIs` had no rule for a failing server: per-server try with a 10 s
   timeout, a 401 retried through `localServer`, errors skipped, stopped
   local servers started before a path is taken as nobody's (2.3).
8. The client lacked the format: `reader()` gains `format` and `ext`;
   `carriesToServer` and `carriesAway` (every id, by the row's `_doc`)
   require a PDF; the carried routes and the shell refuse a non-PDF (3.3,
   3.7).
9. Unowned files: `VaultChip.jsx` and `FolderPickerDialog.jsx` go to unit C,
   keep pushing hidden for a non-PDF and refused by `add_to_library`; the
   `libraryUtils.js` comment is written by unit B, its owner (3.7, 5).
10. An opened `.md` added to the library: decided, refused (hidden in the
    client, refused by the server); dropping the file on the library is the
    way to keep it as a note (decision 3).
11. Gamma as the default where nothing else claims a type: confirmed for
    macOS. One standard on every platform (decision 4: never take a double
    click from an app that opens the type; opening an unclaimed type is
    accepted); `.ltx .cls .sty` dropped through the fixture's `left_out`;
    checklist rows for an unclaimed `.typ` on Windows and macOS.
12. The uninstaller left keys it made: `DeleteRegKey /ifempty` of
    `<ext>\OpenWithProgids` then `<ext>`, in uninstall and for a dropped
    extension; openWith.test checks the order (1.2, 1.6).
13. `mime` disagreed with the registry (`formats.py:68,70`): the field is
    dropped and the Store manifest writes no `ContentType` (1.1, 1.3).
14. The MAS config throws without its variables (electron-builder.mas.cjs:
    39-45): the test sets dummy values first (1.6).
15. `/run` and `/opt` too broad: confirmed. Split into "the directory and
    anything inside" (system trees only) and "the directory itself only"
    (`/run`, `/run/media`, `/media`, `/mnt`, `/opt`, `/Volumes`…) (3.2).
16. The drive root was left out of the answer: one function,
    `place_refusal`, gives the text for every case, used by `clean_path`
    and by `where_is`'s `none` (3.1, 3.2).
17. Landing could not tell "left unticked" from "no page yet" and misread
    `#`/`%`: the dialog's state at Save decides "left unticked"; the page
    comes from a new `GET /{id}/page?path=` over the vault's row, the path
    a query parameter (3.4, 3.7).
18. `never_reason` missed `case`: it now reads each directory with
    `selection._read` itself, so every code of the tree is its (3.1).
19. Unit F answered the wrong "Still to decide" item: confirmed (item 6 is
    Markdown inside a repository). Unit F leaves it and records the
    decision beside step 3's "Not built" item (5, 6).
20. The MAS path needed `pickDirectory`'s `defaultPath`: confirmed; the MAS
    branch calls `pickDirectory(…, dir)` itself and passes the choice to
    `addDirectoryVault` (2.5).
21. A Dolphin service menu and a Nemo action are plain files: confirmed.
    Built (1.5): two generated files installed by the `.deb` through
    `deb.fpm` mappings; GNOME Files still has no packaged entry.

**Design review**
1. Focus lost during a round: as code 4; the `tick=` address parameter
   (3.4).
2. `whereIs` against the signed-out case: one stated rule (2.3): the
   window's session on its server while signed in, else `localServer`,
   which is the account `openServer` signs the window in with; a signed-out
   window learns what the path is but an answer that would navigate stops
   with `Sign in to …`. Tests in main.test.
3. A remote kept folder's untracked file was a dead end: as code 6; test in
   test_remote_work_folders.
4. `/run`, `/opt` refuse removable disks and shares: as code 15, with the
   `/run/media/u/disk/paper` test.
5. Three refusal shapes, home words never shown: one `place_refusal`, never
   a 400 for a place; home is checked before the data directory so its
   words show, and the spec says rule 3 already covered it on desktop
   (3.2).
6. Uninstaller keys: as code 12.
7. macOS "never the default": as code 11.
8. Several folders make hidden work folders: one new work folder at a time,
   the others told to wait (2.4, `waiting`).
9. The `link` reason could never fire: the path is classified unresolved,
   resolved only for the data directory and as a second match; a junction
   test (3.1).
10. Landing looked the file up through the folder route: as code 17, from
    the vault's row.
11. Add to library of an opened `.md`: as code 10; the difference from the
    iPad, which imports a `.md` as a page, is in the research note.
12. The broad-place question covered only the verb: `broadOk` is called by
    `addDirectoryVault` and also by a folder's *Sync with this computer…*
    (main.js:1203) and the relink picker (:1748), which do not go through
    `addDirectoryVault` (3.5).
13. Missing tests: the remote-never-asked check, a signed-out window, the
    remote kept folder file, the `/ifempty` deletions, and a cold-launch
    e2e step are added (unit A, D, E).
