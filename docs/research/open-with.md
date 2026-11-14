# Open with Gamma: folders and files from the file manager

Asked 2026-11-13: can a folder be right-clicked and opened with Gamma, as
*Open with Code* opens one in VS Code, and can a `.md` note, a `.tex` or
another text file be opened with Gamma from the file manager, as a PDF can?
What can each operating system register, and what should opening do, given
the work-folder design of [file-explorer.md](file-explorer.md)?

Status: **decided, not built.** This note surveys what other apps register,
what each target allows, what Gamma had, and records the decision and why.
The build plan, unit by unit with its tests and manual checks, is
[open-with-plan.md](open-with-plan.md). The mechanics, once built, live in
[desktop/docs/architecture.md](../../desktop/docs/architecture.md) "Opening
files and folders", [dev/synced_vaults.md](../dev/synced_vaults.md) "Opened
files" and [dev/work_folders.md](../dev/work_folders.md) "The tracking
dialog".

## The answer in one paragraph

Yes for files on every target but the Linux package and the Mac App Store
build, and yes for folders on Windows (classic menu), macOS (a drop on the
Dock icon, `open -a`) and Linux under KDE's Dolphin and Cinnamon's Nemo (a
service menu and an action, plain files), with no native code. A folder
opened with Gamma opens what Gamma already keeps there, or becomes a work
folder of this computer's own server, the tracking dialog open, exactly as
*Add a synced vault…* makes one. A file never makes a work folder by
itself: inside a vault it is that vault's page (a work folder's untracked
file opens the dialog with it ticked), outside every vault it is a page
under *Opened files*, read where it is and read-only, in the viewer of its
format. A Markdown file outside every vault asks first: *Edit in Gamma*
makes its repository (or its directory) a work folder with the file ticked,
so it becomes a note whose edits are written back; *Just view it* reads it
in the code view. Gamma is listed for the paper's sources (PDF, Markdown, LaTeX,
BibTeX, Typst), derived from the format registry, and never takes a double
click from an app that opens the type. The Windows 11 first menu, a folder
verb in the Store package, a Finder entry and a GNOME Files entry need
native code or a script each user installs, and are left for later.

## What other apps do

- **VS Code** (Windows, Inno Setup). Installer tasks the user can untick:
  *Open with Code* on every file (`*\shell`), on folders
  (`Directory\shell`), on a folder's background (`Directory\Background\shell`)
  and on drives (`Drive\shell`), each command `"Code.exe" "%V"` (or `"%1"`
  for files); *Register Code as an editor for supported file types* adds a
  ProgID per extension (`VSCode.<ext>`) to each extension's `OpenWithProgids`
  without taking the default. On Windows 11 the first context menu needs an
  `IExplorerCommand` COM object and package identity, so VS Code installs a
  small native DLL and registers a signed sparse MSIX package
  (`Add-AppxPackage -ExternalLocation`) beside the classic install, and
  removes it before an update replaces files. On macOS it declares many
  document types and `public.folder` in `CFBundleDocumentTypes`, so a folder
  dropped on its icon opens. Its Linux desktop entry lists
  `inode/directory`, which on some desktops made VS Code the folder opener,
  a long-standing complaint.
- **Sublime Text, Cursor** add *Open with …* entries the same classic way.
- **JetBrains IDEs** offer *Open Folder as Project* as an installer option.
- **Obsidian** adds nothing to the file manager: a vault is opened from inside
  the app (*Open folder as vault*). **GitHub Desktop** adds nothing either.
- **Gamma's own iPad app** declares PDF, Markdown, notebooks and pictures at
  rank *Alternate* and imports a copy ([dev/ipad.md](../dev/ipad.md)); the
  desktop reads in place instead, so a `.md` differs between them by design.

## What each target allows

| Target | Files | A folder |
|---|---|---|
| Windows, NSIS (per user: HKCU, per machine: HKLM) | A ProgID per kind, added to each extension's `OpenWithProgids` and to the app's `Capabilities\FileAssociations` (Default apps). Never the extension's default value; a user's choice is never changed. An extension with no key before (`.typ`) gets one, which the uninstaller removes again while empty (`DeleteRegKey /ifempty`). | Classic verbs on `Directory` and `Directory\Background` (`%V`; `%1` is empty on a background). On Windows 11 they show under *Show more options*; the first menu needs the COM DLL and a signed sparse package. `Folder` would also match Control Panel, Libraries and zip folders. `"%V"` of a drive root arrives as `D:"` (`\"` is an escaped quote). Several selected run the verb once each. |
| Windows, Store (MSIX) | `windows.fileTypeAssociation`, one per kind. Windows asks before a new app becomes a default. | Only through `windows.fileExplorerContextMenus`, which always names a COM class served by a DLL in the package; registry writes from the packaged app are virtualized and Explorer never sees them. |
| macOS (dmg, zip) | `CFBundleDocumentTypes` at `LSHandlerRank` *Alternate*: listed in *Open With*, never chosen over an app that claims the type; where no app claims it (`.typ` on most Macs), Launch Services picks the only claimant, so a double click opens it. | `public.folder` lets a folder be dropped on the Dock or app icon and `open -a Gamma <dir>` work. Finder has no *Open With* for folders; a context entry needs a Services provider, a Quick Action the user installs, or a Finder Sync extension. |
| Mac App Store | As macOS, but the sandbox grants a file or folder from Finder for one run only; a bookmark for later runs comes only from the open panel. | As macOS, then the picker anyway. |
| Linux `.deb` | `MimeType` in the desktop entry. Where nothing names a default for a type, GLib prefers an application that lists that type exactly over one that lists its parent (`text/plain`), so a listed `text/markdown` can take the double click from the text editor. | `inode/directory` in `MimeType`, with the same risk for every folder (VS Code's case). Without it: KDE's Dolphin reads a service menu file (`/usr/share/kio/servicemenus/`, KF5's `/usr/share/kservices5/ServiceMenus/`) and Cinnamon's Nemo an action file (`/usr/share/nemo/actions/`), plain files a package can install, which are no application entries and claim no default. GNOME's Files (Ubuntu's default) takes Python extensions or per-user scripts only. |

## What Gamma had

PDFs only: `installer.nsh` wrote the `Gamma.pdf` ProgID and Default apps
entry, the Store manifest one file type, macOS an *Alternate* viewer, the
`.deb` `application/pdf`. The shell kept only `.pdf` arguments, macOS's
`open-file` dropped anything else, and the server's open route refused other
suffixes even for a work folder's own file. A dev start passed the app's own
directory as an argument, harmless only because it is no `.pdf`. The pieces
the feature needs were there: *Add a synced vault…* makes a work folder of a
directory with no picker when given one, the window opens a folder, a page or
a tracking dialog by ids, and *Opened files* reads a PDF in place.

## Decided

1. **A folder opened with Gamma** opens the vault there (its folder, or the
   folder of the directory inside it, the list view), a work folder still
   waiting for its files reopening its dialog; a directory around vaults
   opens the one it holds, since vaults never overlap; anything else becomes
   a work folder of this computer's own server. The verb is the person's
   ask, which the plan requires before Gamma writes its marker into a
   directory. A vault of another server opens on that server by its ids,
   never by a path.
2. **A file opened with Gamma never makes a work folder by itself.** A
   double click is no ask to mark its directory, and making one would put
   `.gamma/` into Downloads or the Desktop, spend the ten vaults a workspace
   may have, and block the repository around it later. Inside a vault the
   file is that vault's page (one replacer per file); a work folder's file it
   does not track opens the tracking dialog with that file ticked, and after
   Save the window lands on its page. Outside every vault it is an *Opened
   files* page, read in place. The one way a file leads to a work folder is
   the person's answer to the question of 3.
3. **A Markdown file outside every vault asks: *Edit in Gamma* or *Just view
   it*.** A note is editable only through a vault that pulls: its round
   writes Gamma's edits to the file and settles an edit made in another
   editor meanwhile, a rename and a second copy. A lone file has no round,
   and a vault of one file writes nothing on disk, so Gamma never writes to
   a lone file. The first design therefore opened it read-only and nothing
   else; the owner chose (2026-11-13) to ask, since editing is what a person
   opening a `.md` with Gamma most likely wants. *Edit in Gamma* makes a
   work folder of the file's project, the repository it is in (the nearest
   directory holding `.git`, below home) or else its own directory, as the
   folder verb does: refused places refused, the Desktop, Documents and
   Downloads asking again, one new work folder at a time. A repository's
   root rather than the file's directory, because vaults never overlap and a
   work folder at `repo/docs/` would later refuse `repo/`. Its tracking
   dialog opens with the file ticked, and after Save the window lands on the
   note. *Just view it* reads it as text in the code view, read-only. Such a
   page is never added to the library: every `.md` in the library is a note,
   and a text document named `.md` would be the one exception (a pulling
   vault would write it out as a file its next scan calls a note). Dropping
   the file on the library imports it as a note, a copy. Gamma's iPad app
   differs on purpose: it imports a `.md` it is handed as a page, a copy,
   where the desktop reads the file in place.
4. **Listed for the paper's sources only**: PDF, Markdown, and LaTeX, BibTeX
   and Typst sources (`.pdf .md .markdown .tex .bib .typ`), by one rule over
   the format registry, kept in a shared fixture and checked against the
   registry by a test; the LaTeX support files `.ltx .cls .sty` are named
   there as left out (nobody opens them by themselves, and `.cls` is also
   Visual Basic's). Code, data, HTML, pictures and media are not listed
   (they are useful beside their repository, which the folder verb opens,
   and browsers and editors own them), but each still opens when the person
   picks Gamma for it. No verb on every file.
5. **One standard for defaults, every platform**: registering never takes a
   double click from an app that opens the type; where no app opens it, a
   double click opening Gamma read-only is accepted, since it takes nothing
   from anyone. Windows' `OpenWithProgids` and macOS's *Alternate* meet it.
   A Linux `MimeType` may not (GLib's exact-type preference can take
   `text/markdown` from the text editor), so the `.deb` keeps listing PDFs
   only until that is checked; its folder entries are no application
   entries and cannot take anything.
6. **No native code in this step.** The Windows 11 first menu, the Store's
   folder verb and a Finder context entry each need a native component and,
   on Windows, a signed sparse package that local and fork builds cannot
   register. The classic verb works on Windows 10 and under *Show more
   options* on 11. On Linux, Dolphin's service menu and Nemo's action are
   plain files, so they are built; GNOME Files has nothing a package can
   install.
7. **Refused places**: a drive root and Gamma's data directory as before, and
   now the home directory, any directory around it, the system's own trees
   (and what is inside them), and the bare mount and temporary directories
   (`/run`, `/media`, `/mnt`, `/opt`, `/Volumes`…, never what is inside
   them: removable disks and shares mount there), for every way a vault is
   made, in one function's words. The Desktop, Documents, Downloads, the
   picture, music and video folders and a cloud drive's root ask once before
   they become a vault's directory, whichever way the shell is asked.
8. **One new work folder at a time.** Explorer runs the verb once for each
   selected folder; while a work folder the verb made still waits for its
   files, the others are told to wait, so a slip on many folders writes one
   marker.
9. **The shell asks, the page gets ids.** Which vault holds a path is asked
   of every local server (a stopped one started first) as one stated
   account per server; a remote server is never asked. The one path that
   reaches the page is a file's path relative to its work folder, for the
   dialog's tick, in the address beside the link's id and dropped from it
   at once.
10. **English entries.** The installer, the Store package, the Linux
    entries and the desktop shell have no translations; what the web app
    shows is translated.

## Still open

- The Windows 11 first-menu entry and a Store folder verb (the DLL from
  VS Code's approach, its build and signing in the release workflow).
- A Finder Quick Action or Finder Sync extension; a GNOME Files entry (a
  nautilus-python extension, or a script the user installs).
- Markdown and LaTeX on Linux, once checked that listing them moves no
  default on stock Ubuntu and Fedora.
- Making the context entries optional in the installer.
- A remembered answer to the Markdown question (*always view*, *always
  edit*), which would need a setting to take it back.
- An *Opened files* page whose directory later becomes a work folder keeps
  its own page (as a PDF does); taking it in is open.
- A budget for the vault preview's walk of a very large directory.

## Relation to earlier notes

- [file-explorer.md](file-explorer.md) decided that a directory comes into
  Gamma as a work folder, that files other than notes are read-only, and
  what Gamma may write into a directory. This note follows those decisions
  and builds what its step 3 left out (*Opened files* of formats other than
  PDF). It decides a question that plan did not ask (a Markdown file outside
  every vault asks whether to become a note of a work folder); its "Still to
  decide" 6, Markdown inside a repository's work folder, stays open.
- [vaults.md](vaults.md) "Reading a PDF where it is" is the precedent for a
  file opened by itself; this note widens it to every format Gamma can show.
- [agent-collab.md](agent-collab.md): the verb only makes the work folder an
  agent needs quicker to reach; it never turns an agent on.
