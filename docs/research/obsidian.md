# Obsidian: the vault format, and what an import/export must speak

Survey made in September 2026 from the official help site (`obsidian.md/help/…`),
the official Importer plugin's source (`obsidianmd/obsidian-importer`) and
migration write-ups (Logseq, Notion, Bear, Joplin → Obsidian), before the
Obsidian import/export was written. The mechanics Gamma ended up with are in
[docs/dev/import_export.md](../dev/import_export.md); this note keeps the
findings and the reasons behind the mapping.

## What a vault is

A vault is a plain folder. Every `.md` file is a note whose **title is its
filename** (no front-matter `title`, no H1 convention); directories are the
only hierarchy. Next to the notes sit attachments (images `.avif .bmp .gif
.jpeg .jpg .png .svg .webp`, audio, video, `.pdf`), `.canvas` files (JSON
Canvas), `.base` files, and a `.obsidian/` configuration folder (settings,
plugins, `workspace.json`) that carries no note content. There is no manifest
and no id: identity is the path.

Filenames may not contain `[ ] # ^ |` on any OS (they break link syntax),
plus the OS set (`* " \ / < > : ?` on Windows) and may not start with `.`.
The links page lists `# | ^ : %% [[ ]]` as characters that make a note name
unlinkable.

## Obsidian Flavored Markdown

CommonMark + GFM + LaTeX, plus:

- **Wikilinks** `[[Note]]`, `[[Note.md]]`, `[[Folder/Note]]` (vault-root
  path, forward slashes), `[[Note#Heading]]` (chains `#A#B` for nested
  headings), `[[Note#^blockid]]`, `[[#Heading]]` (same note),
  `[[Note|display text]]`, and any combination (`[[Note#Sec|label]]`).
  Non-note targets keep their extension: `[[Figure 1.png]]`. Inside a table
  cell the pipe is escaped: `[[Note\|label]]`.
- **Markdown links** are the alternative the "Use Markdown links" setting
  writes: `[Note](Note.md)`, `[Note](Projects/Note.md)`, `%20` for spaces (or
  `<a path with spaces.md>`), `[x](Note.md#Heading)`.
- **Embeds** are a `!` in front of either link form: `![[Note]]` (whole
  note), `![[Note#Heading]]` (the section), `![[Note#^id]]` (one block —
  a transclusion, kept live), `![[img.png]]`, `![[img.png|300]]` (width),
  `![[img.png|300x200]]`, `![[doc.pdf]]`, `![[doc.pdf#page=3]]`,
  `![[doc.pdf#height=400]]`, audio/video/canvas the same way. Markdown-style
  images take the size in the alt slot: `![alt|300](path)`, `![300](url)`.
  Whether non-numeric pipe text on a wikilink embed is an alt is not
  documented.
- **Block identifiers**: ` ^id` appended to a paragraph's last line, or on
  the bullet line of a list item; for whole lists, quotes, callouts, tables
  and fences the id goes on its own line after the block with blank lines
  around. Ids are Latin letters, digits and dashes (`^quote-of-the-day`;
  auto-generated ones are six hex chars). Parts of quotes, callouts and
  tables cannot be linked.
- **Callouts** `> [!type] Optional title` then `> ` body lines; `+` / `-`
  right after `]` make them foldable (open / collapsed). Types are
  case-insensitive; unknown types render as `note`. The names: `note`,
  `abstract` (`summary`, `tldr`), `info`, `todo`, `tip` (`hint`,
  `important`), `success` (`check`, `done`), `question` (`help`, `faq`),
  `warning` (`caution`, `attention`), `failure` (`fail`, `missing`),
  `danger` (`error`), `bug`, `example`, `quote` (`cite`).
- `==highlight==`, `~~strike~~`, `- [ ]` / `- [x]` tasks (any character in
  the box counts as done; `[/]` and `[-]` are conventions), footnotes
  `[^1]` and inline `^[…]`, `%%comments%%` (inline or a block between `%%`
  lines, hidden in reading view), `$…$` / `$$…$$` math, ` ```mermaid `,
  tables with `\|` escapes. Markdown inside raw HTML elements is not rendered.
- **Tags**: inline `#tag`, letters/digits/`_`/`-`/`/` (nested `#a/b`),
  Unicode allowed, must contain a non-digit, case-insensitive.

## Properties (front matter)

YAML between `---` lines. Types: text, list, number, checkbox, date,
date-time, tags; a name's type is fixed vault-wide once assigned. The
built-ins are `tags`, `aliases`, `cssclasses` (older `tag`, `alias`,
`cssclass` still occur), plus Publish keys (`publish`, `permalink`,
`description`, `image`, `cover`). Lists are written as `- item` lines (flow
`[a, b]` works); tags carry no `#`. A wikilink in a property must be quoted:
`source: "[[paper.pdf]]"`. Nothing else is special: any key is just data.

## Link resolution (partly community knowledge)

A bare `[[Name]]` resolves to the note with that basename anywhere in the
vault, case-insensitively; the "New link format" setting decides what the
app writes (shortest path when possible / relative / absolute) but any of
the three is read. When two notes share a basename the app writes the folder
path; the official Notion importer follows the same rule (`[[Title]]` when
unique, `[[path/Title|Title]]` otherwise). Attachments resolve the same way
by filename, whatever the attachment-folder setting (`attachmentFolderPath`:
vault root, a fixed folder, the note's folder, or a subfolder next to it).

## How the official Importer plugin maps other apps

- **Notion**: strips the 32-hex id from names (`/[ -]?[a-z0-9]{32}(\.|$)/`),
  writes `[[Title]]` (path form when ambiguous), attachments as
  `![[file|file]]`, `<aside>` callouts as `[!important]`, toggles as
  headings, property tables as YAML, tags with spaces → hyphens.
- **Logseq** ("flatten outlines"): `id:: uuid` lines become ` ^shortid`
  suffixes on the previous line, `((uuid))` → `[[Page#^id]]`,
  `{{embed ((uuid))}}` → `![[Page#^id]]`, `{{embed [[Page]]}}` → `![[Page]]`,
  all skipped inside fences; `alias::`/`title::` → `aliases`, `tags::` →
  `tags`, `hl-*` / `ls-*` / `logseq.*` properties dropped; task keywords →
  `[ ]` / `[x]` / `[/]` / `[-]`; bullets that are headings become headings,
  runs of leaf bullets become real lists, lone bullets become paragraphs,
  `#[[Long tag]]` → `#Long-tag`. Bullet-per-line vaults are considered
  awkward in Obsidian, which is why flattening exists.

## What an export from another app is expected to look like

Folder = directory, one `.md` per note named by its title, attachments in one
`attachments/` folder referenced by filename, `[[Title]]` links (path form
when ambiguous), `^id` anchors only on blocks that are actually linked,
metadata as `tags` / `aliases` plus free properties, outlines flattened to
headings, paragraphs and genuine lists, no `.obsidian/` folder.

## What this meant for Gamma

- **Import** is the existing Markdown-zip importer with the vault dialect
  added, not a separate path: wikilinks and embeds (notes, headings, block
  ids, images with sizes, PDFs) resolve vault-wide by basename after the
  relative path, `^id` anchors and heading texts become the block targets of
  `[[Note#…]]` links, `![[Note#^id]]` becomes a Gamma synced block, whole-note
  and section embeds degrade to mentions (a Gamma embed shows one block),
  `tags` become labels, `aliases` are kept, fold markers are dropped from
  callouts, `%%comments%%` are removed in a vault, `.obsidian/`, `.trash/`
  and `.canvas` are skipped. A vault note's title is its filename.
- **Export** writes a vault, not the readable Markdown: the tree is
  flattened the way the Logseq importer does (top-level headings and
  paragraphs, deeper blocks as nested lists), pages link by title
  (`[[Title]]`, `[[Folder/Title]]` when ambiguous), block mentions and synced
  blocks become `[[Title#^id]]` / `![[Title#^id]]` with ` ^id` written on the
  target (a heading too: no `[[Title#Heading]]` is written), highlights are
  `[!quote]` callouts whose title links the bundled PDF's page
  (`[[paper.pdf#page=3|p. 3]]`), the PDF sits in `attachments/` and is the
  quoted `source: "[[paper.pdf]]"` property, labels become `tags`. The Gamma
  format stays the lossless route; the vault is for reading and writing in
  Obsidian and imports back through the vault importer.

## Getting a vault to Gamma, on each device (November 2026)

Surveyed again before the Import dialog got an Obsidian card of its own:
help pages and forum threads (`help.obsidian.md` "Back up your Obsidian
files", the forum's dotfile feature requests), Apple's Files help, the pages
of zip-making community plugins, and Gamma's own opened-file code.

- **Obsidian exports no vault.** There is no "export as zip" command; the
  vault already is a folder, and every guide says to compress it with the
  OS: *Compress* in Finder, *Send to → Compressed (zipped) folder* in
  Explorer, *Compress* in the Files app of an iPhone or iPad (one folder
  gives `<folder>.zip` beside it). Android's stock file managers vary; a
  phone may have no way to make one at all. Files linked from outside the
  vault stay behind in every route. Community plugins zip a vault
  (Vault Backup, Export Folder to ZIP, Differential ZIP Backup, Share as
  ZIP), all desktop-only, and at least one never puts `.obsidian/` or
  `.git/` in the archive.
- **Obsidian reads no dot-path.** Its own filter keeps any file or folder
  whose name starts with `.` out of the file explorer, search, the link
  graph and Bases, and Obsidian Sync does not sync them; plugins (Hidden
  Folders Access, Reveal Hidden Files) exist only to undo that. So
  `.obsidian/`, `.trash/`, a `.git/` (the obsidian-git plugin's, often far
  larger than the notes) or a `.github/` are no part of a vault's content.
- **`.obsidian/` cannot mark a vault.** The importer had told a vault by
  that folder alone. Whether a zip keeps it depends on the tool: Finder's
  *Compress* keeps invisible files (the `.DS_Store` every Mac zip carries),
  and on Windows a leading dot hides nothing, so Explorer zips `.obsidian`
  like any folder (not checked by hand); no source says what the Files
  app's *Compress* does with it; and the plugin above leaves it out on
  purpose. A vault zipped without it read as Plain: titles from the
  H1 instead of the filename, `%%comments%%` kept, an `^id` kept only where
  a link names it.
- **What the native apps already did.** The iPad declares `.zip` among its
  document types and Android among its intent filters, so a vault's zip
  shared from Files or a file manager already reached the Import dialog,
  which offers the sources that read an archive. Neither app takes a
  folder (the iPad refuses one; Android's pickers hand a web view none), so
  on them a vault comes as a zip. The desktop app registers no `.zip`, and
  needs none: its window is the web app, whose folder picker takes the
  vault's folder directly, and a vault kept in step both ways is a synced
  vault ([vaults.md](vaults.md)), not an import.

What this meant for Gamma: an **Obsidian vault** card with a source of its
own, `obsidian`, that tells the Markdown zip reader the upload is a vault,
whatever folders it kept; the vault dialect skipping every dot-path, as
Obsidian does; the card taking the vault's folder where the browser can
pick one, packed without its dot-paths so a `.git` is never uploaded; and
the card offered to a `.zip` the iPad or Android app was given. Not
guessed from the zip: a vault that kept `.obsidian/` reads the same under
Markdown notes, and one without it shows nothing to guess by. `.canvas` and `.base`
files (Obsidian's views, JSON and YAML with no notes in them) stay out with
a warning. Not taken: an iPad document type for a folder, and Excalidraw's
`.excalidraw.md` drawings, which come in as notes of their JSON.
