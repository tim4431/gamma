# Folders on disk: syncing a Gamma folder with a directory on a PC

Thought through in October 2026, before the one-way sync was written. The
question was how to keep files and folders in step between Gamma and
people's own computers, and what to do about Gamma's folders not being a
file tree. Status: **the one-way half, Gamma to disk, is built**. The
desktop app keeps folders on the PC it runs on, and the same rounds run as
a one-file client ([dev/folder_sync.md](../dev/folder_sync.md)). The way
back is the open item at the end.

## Why not rsync, Syncthing or a mount

- **Wrong data model.** rsync compares two trees of bytes by path. Gamma's
  truth is rows in `pages.db` plus content-addressed uploads. The only
  thing rsync could copy is the data directory, which is a backup rather
  than a folder of PDFs one can open, and copying a live SQLite file is
  unsafe. Off-site copies of the data directory are a separate feature
  (`gamma/offsite.py`), and that is the job rsync-style copying is right
  for.
- **No identity, no base.** rsync keys on paths, so a title change is a
  delete plus a create. It keeps no record of the last synced state, so a
  two-way use cannot tell "deleted here" from "not yet created there".
  Syncthing and Unison keep a base but still key on paths.
- **The mapping needs Gamma.** Pages filed in several folders, folder
  names containing slashes, pages at the library root and a trash that
  must not travel cannot be expressed in bytes. Any tool would need a
  Gamma-aware layer in front of it, and that layer is the whole problem.
- **A WebDAV or FUSE mount** gives no offline copy, is painful on Windows,
  and still needs the same mapping.

So the sync is a client of Gamma's own API, the third consumer of its sync
surface after the server-to-server [mirror](../dev/mirror.md) and the
iPad's replica. For the one-way half the client needs only to read: a
single stdlib-only Python file and a read-scope integration token, no
Gamma installed on the PC.

## A Gamma folder is a real folder, almost

Inside one folder subtree, with each page filed once, Gamma's folders are
a directory tree: folders nest, pages sit in them, nothing else. The
mapping is the trivial one. A directory is a folder, a file stem is a
page title. Three things differ, each with a one-line fix:

1. **A page is identified by id, a file by path.** This matters for a
   rename and for any way back: renaming a notes file must be a title
   change, not a delete plus a create that drops the page's highlights.
   Fix: carry the identity in the file. The page id goes in the Markdown
   front matter (`gamma_id`), and a PDF's identity is its content hash,
   which is already its `doc_id`. Nothing maps paths to ids; the files do.
2. **Gamma allows what a filesystem forbids.** A page in two folders, two
   pages with one title in one folder, slashes in names, names that differ
   only by case, Windows device names. Fix: resolve deterministically at
   the boundary. The first filing below the synced folder wins (the
   export's rule), colliding stems get a numeric suffix, names go through
   the vault export's sanitizer. Making a linked folder refuse double
   filing inside Gamma was considered and rejected: a second kind of folder
   in the app for a rare case.
3. **A page is not one file.** It has a PDF, notes, highlights, ink,
   sheets. Fix: a page is a stem with up to two files, the PDF and the
   notes, plus attachments by hash. That is the Obsidian export's shape
   with the PDF moved from `attachments/` to the note's side, so the
   directory reads as a folder of papers and still opens as a vault.

What a real folder does not remove is the memory every two-way sync needs,
what both sides looked like last time; without it one cannot tell "deleted
on disk" from "not yet written", nor a renamed directory from a new one.
For the one-way half this is a small state file in the destination that
lists what the client wrote. It is also what makes deletion safe: the
client only ever touches files it wrote.

## Decisions taken

- **Scope is a folder, or the library.** A link pairs one Gamma folder
  with one directory; `root` links the whole library. Several links to
  several directories coexist.
- **The server lays out, the client places.** The layout rules (which
  directory, which stem, the suffixes) live once on the server, where the
  folder exports already had them, and are read as a *manifest*. The
  client's job is a diff against its state and file writes. This keeps the
  client at one file and lets the layout evolve without touching PCs.
- **Notes files always, PDFs when stored.** Every page is one `.md` so
  the metadata and a future place to type exist for every paper; a link can
  opt out of notes for a plain folder of PDFs. A proxied PDF never stored
  on the server has no file.
- **Identity for staleness.** A PDF is up to date when its `doc_id` is;
  a notes file carries a `version` the server derives from the page's
  latest op and stamp and its labels' names. A link's text may lag when
  another page's title changes; `--full` rewrites everything.
- **Safety over completeness.** A file changed on disk is kept and
  reported, not overwritten, until `--force`. A file in the way that the
  client did not write is never touched. A directory is removed only when
  empty.
- **Where it runs.** The rounds are one module with two sources: the
  server runs them in-process for the links it keeps of its own
  workspaces (a NAS share then carries the directory to every machine),
  over HTTP for a link whose folder is on another Gamma server, and the
  same file runs alone on a PC. Only the desktop app's own server keeps
  links. A server writing folders to its own disk is not offered: the
  folder is wanted on the PC, which the app reaches with a read token it
  mints and no clone. A folder's right-click menu in Gamma and the bar's
  chooser start it; the app asks for the directory.

## Still open

- **The way back.** Disk to Gamma: a new PDF dropped in a directory
  becomes a page filed in that folder; a new `.md` goes through the
  Markdown importer; an edited `.md` is parsed into blocks, matched by the
  anchors the vault dialect already carries, diffed against the page's
  tree (`sync_tree.diff`) and applied through the normal op path with a
  base, so the server's three-way text merge handles concurrent edits and
  a real conflict surfaces in the existing merge chip. The client would be
  a collaborator, not a replicator. Open choices before building it:
  anchors on every block (lossless, visible in VS Code) or only on linked
  ones; what a delete on disk means (unfile from the folder, trash only
  when that was the page's only filing, is the recommendation); embedded
  PDF annotations made elsewhere, which the annotation importer already
  reads idempotently.
- **An annotated PDF on disk** instead of the original, for reading
  elsewhere, at the price of a rewrite on every highlight and an identity
  held only in the state file.
- **A token's life.** A link with a remote source (the desktop app's way
  of keeping a NAS folder on a PC without a clone: the local server runs
  the client's `RemoteSource` behind a read token the shell mints) holds a
  token that expires as the issuer set it, a year at most; the round then
  reports a refused token and the link has to be made again. A token the
  server could renew, or one without an expiry for a device the user
  owns, would remove that chore.
